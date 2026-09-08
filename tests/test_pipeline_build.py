import json
from unittest.mock import MagicMock

from mkpipe.models import ConnectionConfig, ReplicationMethod, TableConfig
from mkpipe_extractor_mongodb import MongoDBExtractor


def _extractor() -> MongoDBExtractor:
    return MongoDBExtractor(
        ConnectionConfig(
            variant='mongodb', host='h', port=27017, database='db',
            user='u', password='p',
        )
    )


def _table(**kwargs) -> TableConfig:
    defaults = dict(
        name='product',
        target_name='raw__product',
        replication_method=ReplicationMethod.INCREMENTAL,
        iterate_column=['createdDate', 'updatedDate'],
        iterate_column_type='datetime',
    )
    defaults.update(kwargs)
    return TableConfig(**defaults)


class _FakeReader:
    """Captures options; .load() returns a fake df with a canned first row."""

    def __init__(self, spark, row):
        self.spark = spark
        self.row = row
        self.options = {}
        spark.readers.append(self)

    def option(self, key, value):
        self.options[key] = value
        return self

    def load(self):
        df = MagicMock()
        df.first.return_value = self.row
        return df


class _FakeRead:
    def __init__(self, spark):
        self.spark = spark

    def format(self, fmt):
        assert fmt == 'mongodb'
        return _FakeReader(self.spark, self.spark.row)


def _spark(row):
    spark = MagicMock()
    spark.readers = []
    spark.row = row
    spark.read = _FakeRead(spark)
    return spark


def _pipeline(reader):
    return json.loads(reader.options['aggregation.pipeline'])


def test_incremental_with_last_point_watermark_and_data_read():
    ext = _extractor()
    spark = _spark(row={'_id': None, '_mk_m0': '2026-01-01', '_mk_m1': '2026-01-02'})
    table = _table(custom_query='[{"$match": {"x": {"$exists": true}}}]')

    result = ext.extract(table, spark, last_point='2025-12-31')

    wm_reader, data_reader = spark.readers
    # watermark: incremental $match FIRST (partition planning counts only the
    # first $match stage), then custom_query, then $group — single partition
    assert wm_reader.options['partitioner'].endswith('SinglePartitionPartitioner')
    wm = _pipeline(wm_reader)
    assert wm[0] == {
        '$match': {'$or': [{'createdDate': {'$gte': '2025-12-31'}}, {'updatedDate': {'$gte': '2025-12-31'}}]}
    }
    assert wm[1] == {'$match': {'x': {'$exists': True}}}
    assert wm[2]['$group']['_mk_m0'] == {'$max': '$createdDate'}
    assert wm[2]['$group']['_mk_m1'] == {'$max': '$updatedDate'}
    # data read: same filter, no $group, table partitioner untouched
    data = _pipeline(data_reader)
    assert data == wm[:2]
    assert 'SinglePartitioner' not in data_reader.options.get('partitioner', '')
    assert result.write_mode == 'append'
    assert result.last_point_value == '2026-01-02'
    assert result.df is not None


def test_incremental_no_new_data_returns_none_without_data_read():
    ext = _extractor()
    spark = _spark(row=None)
    result = ext.extract(_table(), spark, last_point='2025-12-31')
    assert result.df is None
    assert result.write_mode == 'append'
    assert len(spark.readers) == 1  # only the watermark read


def test_first_run_is_overwrite_and_skips_match_stage():
    ext = _extractor()
    spark = _spark(row={'_id': None, '_mk_m0': '2026-01-01', '_mk_m1': None})
    result = ext.extract(_table(), spark, last_point=None)
    wm, data = spark.readers
    assert _pipeline(wm)[-1].get('$group') is not None
    assert 'aggregation.pipeline' not in data.options
    assert result.write_mode == 'overwrite'
    assert result.last_point_value == '2026-01-01'


def test_empty_first_run_returns_df_with_no_last_point():
    ext = _extractor()
    spark = _spark(row=None)
    result = ext.extract(_table(), spark, last_point=None)
    assert result.df is not None  # empty full load still reaches loader
    assert result.last_point_value is None
    assert result.write_mode == 'overwrite'
