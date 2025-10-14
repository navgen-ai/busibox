from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection, utility

DIM=1024
ROLE_PARTITIONS=True

connections.connect("default", host="10.96.200.23", port="19530")

fields = [
    FieldSchema(name="vector_id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="upload_id", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="role", dtype=DataType.VARCHAR, max_length=128),
    FieldSchema(name="chunk_index", dtype=DataType.INT64),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=DIM),
]

schema = CollectionSchema(fields, description="Document chunks")
name = "doc_chunks"

if utility.has_collection(name):
    print("Collection exists")
    coll = Collection(name)
else:
    coll = Collection(name, schema)
    coll.create_index(field_name="vector", index_params={"index_type":"HNSW","metric_type":"COSINE","params":{"M":32,"efConstruction":200}})
    coll.load()
    print("Collection created")

print("Ready.")
