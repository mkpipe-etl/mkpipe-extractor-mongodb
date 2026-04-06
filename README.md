# mkpipe-extractor-mongodb

MongoDB extractor plugin for [MkPipe](https://github.com/mkpipe-etl/mkpipe). Reads MongoDB collections into Spark DataFrames using the official MongoDB Spark Connector.

## Documentation

For more detailed documentation, please visit the [GitHub repository](https://github.com/mkpipe-etl/mkpipe).

## License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.

---

## Connection Configuration

```yaml
connections:
  mongo_source:
    variant: mongodb
    mongo_uri: "mongodb://user:password@host:27017/mydb?authSource=admin"
    database: mydb
```

Alternatively, use individual parameters (URI is constructed automatically):

```yaml
connections:
  mongo_source:
    variant: mongodb
    host: localhost
    port: 27017
    database: mydb
    user: myuser
    password: mypassword
```

---

## Table Configuration

```yaml
pipelines:
  - name: mongo_to_pg
    source: mongo_source
    destination: pg_target
    tables:
      - name: events
        target_name: stg_events
        replication_method: full
```

### Incremental Replication

```yaml
- name: events
  target_name: stg_events
  replication_method: incremental
  iterate_column: updated_at
  iterate_column_type: datetime
```

### Custom Aggregation Pipeline

```yaml
- name: orders
  target_name: stg_orders
  replication_method: full
  custom_query: '[{"$match": {"status": "active"}}, {"$project": {"_id": 0}}]'
```

---

## Connection-Level Extra Options

Any key-value pairs under `extra` are forwarded directly as Spark read options. This is useful for setting defaults that apply to all tables using this connection (e.g. a partitioner for Amazon DocumentDB).

```yaml
connections:
  docdb_source:
    variant: mongodb
    mongo_uri: "mongodb://user:pass@docdb-host:27017/mydb?authMechanism=SCRAM-SHA-1"
    database: mydb
    extra:
      partitioner: com.mongodb.spark.sql.connector.read.partitioner.SinglePartitionPartitioner
```

---

## Read Parallelism (Partitioners)

By default the MongoDB Spark Connector uses `AutoBucketPartitioner`, which relies on the `$bucketAuto` aggregation stage. This works on MongoDB but **does not work on Amazon DocumentDB** (which does not support `$bucketAuto`). For DocumentDB or when you need explicit control, configure one of the alternative partitioners below.

### Available Partitioners (Spark Connector v10.x)

All class names are under the `com.mongodb.spark.sql.connector.read.partitioner` package.

| Class Name                          | `partitioner` value (full class path)                                                          | Best For                                                                             |
| ----------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `AutoBucketPartitioner`             | `com.mongodb.spark.sql.connector.read.partitioner.AutoBucketPartitioner`                       | Default — uses `$bucketAuto` (not supported on DocumentDB)                           |
| `SamplePartitioner`                 | `com.mongodb.spark.sql.connector.read.partitioner.SamplePartitioner`                           | General-purpose large collections — uses `$sample` (works on DocumentDB)             |
| `PaginateBySizePartitioner`         | `com.mongodb.spark.sql.connector.read.partitioner.PaginateBySizePartitioner`                   | When you want partitions based on data size                                          |
| `PaginateIntoPartitionsPartitioner` | `com.mongodb.spark.sql.connector.read.partitioner.PaginateIntoPartitionsPartitioner`           | When you want a fixed number of partitions                                           |
| `ShardedPartitioner`                | `com.mongodb.spark.sql.connector.read.partitioner.ShardedPartitioner`                          | Sharded MongoDB clusters                                                             |
| `SinglePartitionPartitioner`        | `com.mongodb.spark.sql.connector.read.partitioner.SinglePartitionPartitioner`                  | Small collections, or safest fallback for DocumentDB                                 |

### Configuration

```yaml
- name: events
  target_name: stg_events
  replication_method: full
  partitioner: com.mongodb.spark.sql.connector.read.partitioner.SamplePartitioner
  partitioner_options:
    partition.size: "64"          # target partition size in MB (default: 64)
    samples.per.partition: "10"   # sample points per partition (default: 10)
```

#### PaginateIntoPartitionsPartitioner — fixed partition count

```yaml
partitioner: com.mongodb.spark.sql.connector.read.partitioner.PaginateIntoPartitionsPartitioner
partitioner_options:
  max.number.of.partitions: "8"
```

#### SinglePartitionPartitioner — no partitioning

```yaml
partitioner: com.mongodb.spark.sql.connector.read.partitioner.SinglePartitionPartitioner
```

### Amazon DocumentDB Compatibility

DocumentDB does not support `$bucketAuto`. Use one of:

- **`SamplePartitioner`** — uses `$sample`, supported by DocumentDB. Good balance of parallelism and compatibility.
- **`SinglePartitionPartitioner`** — safest option, no special aggregation stages required. All data is read in a single partition; use `write_partitions` in the loader or Spark repartitioning for downstream parallelism.

You can set the partitioner per-table (as shown above) or per-connection via `extra` (see Connection-Level Extra Options).

### Performance Notes

- **Small collections (<1M docs):** partitioner overhead is not worth it — omit it or use `SinglePartitionPartitioner`.
- **Large collections (>5M docs):** `SamplePartitioner` is a safe default. Expect 3–10x read speed-up when Spark has multiple cores/executors.
- **`partition.size`**: lower values → more, smaller partitions → more parallelism but more MongoDB connections.
- The partitioner only affects the **read** side. Write parallelism is controlled by `write_partitions` in the loader.

---

## All Table Parameters

| Parameter             | Type                   | Default  | Description                                      |
| --------------------- | ---------------------- | -------- | ------------------------------------------------ |
| `name`                | string                 | required | MongoDB collection name                          |
| `target_name`         | string                 | required | Destination table/collection name                |
| `replication_method`  | `full` / `incremental` | `full`   | Replication strategy                             |
| `iterate_column`      | string                 | —        | Column used for incremental watermark            |
| `iterate_column_type` | string                 | —        | Type hint for watermark column                   |
| `custom_query`        | string                 | —        | MongoDB aggregation pipeline (JSON array string) |
| `partitioner`         | string                 | —        | MongoDB Spark partitioner class name             |
| `partitioner_options` | map                    | `{}`     | Key-value options passed to the partitioner      |
| `tags`                | list                   | `[]`     | Tags for selective pipeline execution            |
| `pass_on_error`       | bool                   | `false`  | Skip table on error instead of failing           |
