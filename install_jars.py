import os
import shutil
from pyspark.sql import SparkSession
from pyspark import SparkConf

# Define the connector version
connector_version = 'org.mongodb.spark:mongo-spark-connector_2.13:10.5.0'

# Define the paths
ROOT_DIR = os.path.realpath(os.path.dirname(__file__))
ivy2_dir = os.path.join(ROOT_DIR, '.ivy2')
src_dir = os.path.join(ivy2_dir, 'jars')
dest_dir = os.path.join(ROOT_DIR, 'mkpipe_extractor_mongodb', 'jars')

print(f'Root Directory: {ROOT_DIR}')
print(f'Ivy2 Source Directory: {src_dir}')
print(f'Destination Directory: {dest_dir}')

# Clean the .ivy2 directory
if os.path.exists(ivy2_dir):
    print(f'Cleaning up {ivy2_dir}...')
    shutil.rmtree(ivy2_dir)
else:
    print(f'{ivy2_dir} does not exist, skipping cleanup.')

# Ensure the .ivy2 directory is recreated
os.makedirs(ivy2_dir, exist_ok=True)


# Spark configuration
conf = SparkConf()
conf.setAppName('install_jars')
conf.set('spark.jars.packages', f'{connector_version}')
conf.set('spark.jars.ivy', ivy2_dir)
spark = SparkSession.builder.config(conf=conf).getOrCreate()

# Ensure the destination directory exists
os.makedirs(dest_dir, exist_ok=True)

# Copy the JARs from Ivy2 to the destination directory
if os.path.exists(src_dir):
    print(f'Copying JARs from {src_dir} to {dest_dir}...')
    shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
    print('JARs copied successfully.')
else:
    print(f'No JARs found in {src_dir}. Ensure the package resolves correctly.')

# Clean up the .ivy2 directory
if os.path.exists(ivy2_dir):
    print(f'Cleaning up {ivy2_dir}...')
    shutil.rmtree(ivy2_dir)
    print('Cleanup complete.')
