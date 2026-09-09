from pathlib import Path
import ast


ROOT = Path(__file__).parents[1]


def test_mongo_database_name_is_mongo_valid():
    source = (ROOT / "utils/database.py").read_text()
    tree = ast.parse(source)
    names = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    database_names = [name for name in names if "Apex" in name and "DB" in name]
    assert database_names
    assert all(" " not in name for name in database_names)
    assert "ApexVibesDB" in database_names
