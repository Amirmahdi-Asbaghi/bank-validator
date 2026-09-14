"""Read a Delta table from MinIO and print rows.

Usage:
    spark-submit show_delta.py <bucket> <run_id> [error_code]

Examples:
    spark-submit show_delta.py curated ee93d797-5658-4b34-b18d-254a189402d4
    spark-submit show_delta.py quarantine ee93d797-5658-4b34-b18d-254a189402d4 E007
"""
import sys
from pyspark.sql import SparkSession, functions as F

bucket = sys.argv[1] if len(sys.argv) > 1 else "curated"
run_id = sys.argv[2] if len(sys.argv) > 2 else None
error_code = sys.argv[3] if len(sys.argv) > 3 else None

if not run_id:
    print("Usage: show_delta.py <curated|quarantine> <run_id> [error_code]")
    sys.exit(1)

spark = (
    SparkSession.builder
    .appName("show-delta")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
    .config("spark.hadoop.fs.s3a.access.key", "minioadmin")
    .config("spark.hadoop.fs.s3a.secret.key", "minioadmin")
    .config("spark.hadoop.fs.s3a.path.style.access", "true")
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

path = f"s3a://{bucket}/runs/{run_id}/"
print(f"\n=== Reading {path} ===\n")

try:
    df = spark.read.format("delta").load(path)
except Exception as e:
    print(f"Failed to read: {e}")
    sys.exit(1)

print(f"Total rows: {df.count()}\n")
print(f"Columns: {df.columns}\n")

if error_code:
    df = df.filter(F.array_contains(F.col("error_codes"), error_code))
    print(f"Rows with {error_code}: {df.count()}\n")

df.show(20, truncate=False)
spark.stop()