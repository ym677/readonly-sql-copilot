from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project import execute_readonly, run


class OfflineClient:
    enabled = False


class SqlGuardTests(unittest.TestCase):
    def test_aggregate_is_correct(self):
        result = execute_readonly("SELECT COUNT(*), MAX(temperature) FROM measurements")
        self.assertEqual(result['rows'], [[9, 76.0]])

    def test_join_is_permitted(self):
        result = execute_readonly("SELECT e.name, m.temperature FROM equipment e JOIN measurements m ON e.equipment_id=m.equipment_id WHERE e.equipment_id='P-101'")
        self.assertEqual(len(result['rows']), 3)

    def test_with_select_is_permitted(self):
        result = execute_readonly("WITH pumps AS (SELECT * FROM equipment WHERE equipment_id='P-101') SELECT name FROM pumps")
        self.assertEqual(result['rows'], [['循环泵']])

    def test_write_rejected(self):
        with self.assertRaises(ValueError):
            execute_readonly("DELETE FROM equipment")

    def test_multiple_statements_rejected(self):
        with self.assertRaises(ValueError):
            execute_readonly("SELECT * FROM equipment; DROP TABLE equipment")

    def test_attach_rejected(self):
        with self.assertRaises(ValueError):
            execute_readonly("ATTACH DATABASE '/tmp/external.db' AS ext")

    def test_extension_function_rejected(self):
        with self.assertRaises(ValueError):
            execute_readonly("SELECT load_extension('example')")

    def test_large_allocation_function_rejected(self):
        with self.assertRaises(ValueError):
            execute_readonly("SELECT randomblob(1000000000)")

    def test_system_table_rejected(self):
        with self.assertRaises(ValueError):
            execute_readonly("SELECT sql FROM sqlite_master")

    def test_recursive_query_rejected(self):
        with self.assertRaises(ValueError):
            execute_readonly("WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<100000) SELECT max(n) FROM x")

    def test_empty_input_rejected(self):
        with self.assertRaises(ValueError):
            run({'text': ' '}, OfflineClient())

    def test_demo_returns_actual_query_result(self):
        result = run({'text': '平均温度'}, OfflineClient())
        self.assertEqual(result['sections'][1]['table']['rows'][0][0], 'M-303')


if __name__ == '__main__':
    unittest.main()
