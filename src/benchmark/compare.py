import pandas as pd
import time
import os

def benchmark_formats(filepath: str):
    """Compares CSV, JSON, and Parquet performance metrics."""
    print("Benchmarking file formats...")
    df = pd.read_parquet(filepath)
    
    formats = {
        'CSV': {'write': lambda d, f: d.to_csv(f, index=False), 'read': pd.read_csv, 'ext': '.csv'},
        'JSON': {'write': lambda d, f: d.to_json(f, orient='records'), 'read': pd.read_json, 'ext': '.json'},
        'Parquet': {'write': lambda d, f: d.to_parquet(f, index=False), 'read': pd.read_parquet, 'ext': '.parquet'}
    }

    for name, funcs in formats.items():
        test_file = f"data/benchmark_test{funcs['ext']}"
        
        start_write = time.time()
        funcs['write'](df, test_file)
        write_time = time.time() - start_write
        
        start_read = time.time()
        funcs['read'](test_file)
        read_time = time.time() - start_read
        
        size_mb = os.path.getsize(test_file) / (1024 * 1024)
        print(f"[{name}] Size: {size_mb:.2f} MB | Write: {write_time:.2f}s | Read: {read_time:.2f}s")
        os.remove(test_file)