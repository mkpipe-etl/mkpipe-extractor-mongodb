import json
from typing import Optional
from urllib.parse import parse_qs, urlparse

from mkpipe.spark.base import BaseExtractor
from mkpipe.models import ConnectionConfig, ExtractResult, TableConfig
from mkpipe.utils import get_logger

JAR_PACKAGES = ['org.mongodb.spark:mongo-spark-connector_2.13:10.5.0']

logger = get_logger(__name__)

_JVM_TLS_CONFIGURED = False


def _is_tls_insecure(uri: str) -> bool:
    """Check if the MongoDB URI requests insecure TLS."""
    try:
        parsed = urlparse(uri)
        params = parse_qs(parsed.query)
        for key in ('tlsInsecure', 'tlsAllowInvalidCertificates'):
            values = params.get(key, [])
            if any(v.lower() in ('true', '1') for v in values):
                return True
    except Exception:
        logger.debug('Failed to parse MongoDB URI for TLS flags, falling back to substring match')
        uri_lower = uri.lower()
        return 'tlsinsecure=true' in uri_lower or 'tlsallowinvalidcertificates=true' in uri_lower

    return False


def _configure_jvm_tls_insecure(spark) -> None:
    """Install a trust-all SSLContext as the JVM default.

    The MongoDB Spark Connector delegates TLS to the JVM default
    ``SSLContext``.  Even when ``tlsInsecure=true`` is present in the
    connection URI, the connector still uses the JVM default trust
    manager which triggers PKIX errors for self-signed or untrusted
    certificates.

    This calls ``com.mkpipe.ssl.TrustAllManager.install()`` — a small
    helper class bundled in ``mkpipe-tls-helper.jar`` — which replaces
    the JVM-wide default ``SSLContext`` with one that accepts all
    certificates.

    .. warning::
        This affects **all** TLS connections in the JVM, not just MongoDB.
    """
    global _JVM_TLS_CONFIGURED  # noqa: PLW0603
    if _JVM_TLS_CONFIGURED:
        return

    try:
        jvm = spark.sparkContext._jvm
        jvm.com.mkpipe.ssl.TrustAllManager.install()
    except Exception as exc:
        raise RuntimeError(
            'Failed to install trust-all SSLContext. '
            'Ensure mkpipe-tls-helper.jar is on the Spark classpath.'
        ) from exc

    _JVM_TLS_CONFIGURED = True
    logger.warning(
        'Installed trust-all SSLContext — all JVM TLS connections '
        'will skip certificate validation'
    )


class MongoDBExtractor(BaseExtractor, variant='mongodb'):
    def __init__(self, connection: ConnectionConfig):
        self.connection = connection
        self.mongo_uri = connection.mongo_uri or (
            f'mongodb://{connection.user}:{connection.password}'
            f'@{connection.host}:{connection.port or 27017}/{connection.database}'
        )
        self.database = connection.database

    def _make_reader(
        self,
        spark,
        table: TableConfig,
        pipeline_stages: Optional[list] = None,
        single_partition: bool = False,
    ):
        reader = (
            spark.read.format('mongodb')
            .option('connection.uri', self.mongo_uri)
            .option('database', self.database)
            .option('collection', table.name)
        )
        for key, value in self.connection.extra.items():
            reader = reader.option(key, str(value))
        if single_partition:
            reader = reader.option(
                'partitioner',
                'com.mongodb.spark.sql.connector.read.partitioner.SinglePartitioner',
            )
        elif table.partitioner:
            reader = reader.option('partitioner', table.partitioner)
            for key, value in table.partitioner_options.items():
                reader = reader.option(f'partitioner.options.{key}', str(value))
        if pipeline_stages:
            reader = reader.option('aggregation.pipeline', json.dumps(pipeline_stages))
        return reader

    def _watermark(self, spark, table: TableConfig, base_stages: list) -> Optional[str]:
        """Resolve max(iterate_column) server-side over the same filter as extraction.

        Single partition, single row — a tiny index-backed aggregation instead
        of a Spark-side agg over the full extraction df.
        """
        group_stage = {
            '$group': {
                '_id': None,
                **{f'_mk_m{i}': {'$max': f'${c}'} for i, c in enumerate(table.iterate_columns)},
            }
        }
        row = (
            self._make_reader(spark, table, base_stages + [group_stage], single_partition=True)
            .load()
            .first()
        )
        if row is None:
            return None
        values = [row[f'_mk_m{i}'] for i in range(len(table.iterate_columns))]
        values = [v for v in values if v is not None]
        return str(max(values)) if values else None

    def extract(self, table: TableConfig, spark, last_point: Optional[str] = None) -> ExtractResult:
        if _is_tls_insecure(self.mongo_uri):
            _configure_jvm_tls_insecure(spark)

        logger.info({
            'table': table.target_name,
            'status': 'extracting',
            'replication_method': table.replication_method.value,
        })

        pipeline_stages = json.loads(table.custom_query) if table.custom_query else []
        is_incremental = table.replication_method.value == 'incremental' and table.iterate_column
        has_static_bounds = table.filter_lower_bound is not None or table.filter_upper_bound is not None
        columns = table.iterate_columns
        is_multi = table.is_multi_iterate_column

        if is_incremental and (has_static_bounds or last_point):
            match_conditions = {}
            if has_static_bounds:
                if table.filter_lower_bound is not None:
                    match_conditions['$gte'] = table.filter_lower_bound
                if table.filter_upper_bound is not None:
                    match_conditions['$lt'] = table.filter_upper_bound
            else:
                match_conditions['$gte'] = last_point
            if is_multi:
                pipeline_stages = pipeline_stages + [
                    {'$match': {'$or': [{col: dict(match_conditions)} for col in columns]}}
                ]
            else:
                pipeline_stages = pipeline_stages + [{'$match': {columns[0]: match_conditions}}]
            write_mode = 'append'
        else:
            write_mode = 'overwrite'

        last_point_value = None
        if is_incremental:
            last_point_value = self._watermark(spark, table, pipeline_stages)
            if last_point_value is None:
                if write_mode == 'overwrite':
                    logger.info({'table': table.target_name, 'status': 'empty_source_initial_load'})
                else:
                    logger.info({'table': table.target_name, 'status': 'no_new_data'})
                    return ExtractResult(df=None, write_mode=write_mode)

        df = self._make_reader(spark, table, pipeline_stages).load()

        logger.info({
            'table': table.target_name,
            'status': 'extracted',
            'write_mode': write_mode,
        })

        return ExtractResult(df=df, write_mode=write_mode, last_point_value=last_point_value)
