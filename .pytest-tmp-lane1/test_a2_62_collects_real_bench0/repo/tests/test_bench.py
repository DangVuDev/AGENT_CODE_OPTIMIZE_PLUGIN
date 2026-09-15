import sys
sys.path.insert(0, "src")
from main import add

def test_add_benchmark(benchmark):
    result = benchmark(add, 2, 3)
    assert result == 5
