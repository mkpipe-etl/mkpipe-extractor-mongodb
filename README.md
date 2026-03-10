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
    mongo_uri: 'mongodb://user:password@host:27017/mydb?authSource=admin'
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

## Read Parallelism (Partitioners)

By default the MongoDB Spark Connector reads with a single partition (`MongoSinglePartitioner`), which means all data flows through one Spark task. For large collections, configuring a partitioner gives a significant speed-up by splitting the read across multiple parallel Spark tasks.

### Available Partitioners

| Partitioner | Best For |
|---|---|
| `MongoSamplePartitioner` | General-purpose large collections (samples the collection to determine split points) |
| `MongoPaginateByCountPartitioner` | When you want an exact number of partitions |
| `MongoPaginateIntoPartitionsPartitioner` | When you want to control records per partition |
| `MongoShardedPartitioner` | Sharded MongoDB clusters |
| `MongoSplitVectorPartitioner` | Uses MongoDB's `splitVector` command (requires admin privileges) |
| `MongoSinglePartitioner` | Small collections or when parallelism is not needed (default) |

### Configuration

```yaml
      - name: events
        target_name: stg_events
        replication_method: full
        partitioner: MongoSamplePartitioner
        partitioner_options:
          partition.size: 64          # target partition size in MB (default: 64)
          samples.per.partition: 10   # sample points per partition (default: 10)
```

#### MongoPaginateByCountPartitioner — exact partition count

```yaml
        partitioner: MongoPaginateByCountPartitioner
        partitioner_options:
          numberOfPartitions: 8       # total number of partitions
          partitionKey: _id           # field to paginate on (default: _id)
```

#### MongoPaginateIntoPartitionsPartitioner — records per partition

```yaml
        partitioner: MongoPaginateIntoPartitionsPartitioner
        partitioner_options:
          numberOfPartitions: 8
```

### Performance Notes

- **Small collections (<1M docs):** partitioner overhead is not worth it — omit it.
- **Large collections (>5M docs):** `MongoSamplePartitioner` is a safe default. Expect 3–10x read speed-up when Spark has multiple cores/executors.
- **`partition.size`**: lower values → more, smaller partitions → more parallelism but more MongoDB connections.
- The partitioner only affects the **read** side. Write parallelism is controlled by `write_partitions` in the loader.

---

## All Table Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | MongoDB collection name |
| `target_name` | string | required | Destination table/collection name |
| `replication_method` | `full` / `incremental` | `full` | Replication strategy |
| `iterate_column` | string | — | Column used for incremental watermark |
| `iterate_column_type` | string | — | Type hint for watermark column |
| `custom_query` | string | — | MongoDB aggregation pipeline (JSON array string) |
| `partitioner` | string | — | MongoDB Spark partitioner class name |
| `partitioner_options` | map | `{}` | Key-value options passed to the partitioner |
| `tags` | list | `[]` | Tags for selective pipeline execution |
| `pass_on_error` | bool | `false` | Skip table on error instead of failing |