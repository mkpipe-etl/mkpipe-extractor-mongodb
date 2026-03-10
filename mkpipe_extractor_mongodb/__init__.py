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

    def extract(self, table: TableConfig, spark, last_point: Optional[str] = None) -> ExtractResult:
        if _is_tls_insecure(self.mongo_uri):
            _configure_jvm_tls_insecure(spark)

        logger.info({
            'table': table.target_name,
            'status': 'extracting',
            'replication_method': table.replication_method.value,
        })

        collection = table.name
        reader = (
            spark.read.format('mongodb')
            .option('connection.uri', self.mongo_uri)
            .option('database', self.database)
            .option('collection', collection)
        )

        if table.partitioner:
            reader = reader.option('partitioner', table.partitioner)
            for key, value in table.partitioner_options.items():
                reader = reader.option(f'partitioner.options.{key}', str(value))

        if table.custom_query:
            reader = reader.option('aggregation.pipeline', table.custom_query)

        if table.replication_method.value == 'incremental' and last_point and table.iterate_column:
            pipeline = f'{{"$match": {{"{table.iterate_column}": {{"$gte": "{last_point}"}}}}}}'
            if table.custom_query:
                existing = json.loads(table.custom_query)
                existing.append(json.loads(pipeline))
                reader = reader.option('aggregation.pipeline', json.dumps(existing))
            else:
                reader = reader.option('aggregation.pipeline', f'[{pipeline}]')
            write_mode = 'append'
        else:
            write_mode = 'overwrite'

        df = reader.load()

        last_point_value = None
        if table.replication_method.value == 'incremental' and table.iterate_column:
            from pyspark.sql import functions as F
            row = df.agg(F.max(table.iterate_column).alias('max_val')).first()
            if row and row['max_val'] is not None:
                last_point_value = str(row['max_val'])

        logger.info({
            'table': table.target_name,
            'status': 'extracted',
            'write_mode': write_mode,
        })

        return ExtractResult(df=df, write_mode=write_mode, last_point_value=last_point_value)
