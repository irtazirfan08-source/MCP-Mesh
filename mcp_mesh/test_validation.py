import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.validator import ToolValidator


def run_tests():
  schema = {
      "type": "object",
      "properties": {
          "query": {"type": "string"},
          "limit": {"type": "integer"},
          "dry_run": {"type": "boolean"},
      },
      "required": ["query"],
  }

  valid_payload = {
      "query": "SELECT * FROM events",
      "limit": 10,
      "dry_run": False,
  }
  is_valid, err = ToolValidator.validate_payload(schema, valid_payload)
  print(f"Test 1 (Valid): {is_valid}, Error: {err}")

  missing_payload = {"limit": 5}
  is_valid, err = ToolValidator.validate_payload(schema, missing_payload)
  print(f"Test 2 (Missing required): {not is_valid}, Error: {err}")

  invalid_type_payload = {"query": 12345, "limit": "invalid_int"}
  is_valid, err = ToolValidator.validate_payload(schema, invalid_type_payload)
  print(f"Test 3 (Type mismatch): {not is_valid}, Error: {err}")


if __name__ == "__main__":
  run_tests()