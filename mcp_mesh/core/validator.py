from typing import Any, Dict, Optional, Tuple


class SchemaValidationError(Exception):

  def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
    super().__init__(message)
    self.details = details or {}


class ToolValidator:

  @staticmethod
  def validate_payload(
      schema: Dict[str, Any], payload: Dict[str, Any]
  ) -> Tuple[bool, Optional[str]]:
    properties = schema.get("properties", {})
    required_fields = schema.get("required", [])

    for field in required_fields:
      if field not in payload:
        return False, f"Missing required parameter: '{field}'"

    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }

    for key, val in payload.items():
      if key in properties:
        expected_type_str = properties[key].get("type")
        expected_type = type_map.get(expected_type_str)
        if expected_type and not isinstance(val, expected_type):
          return (
              False,
              f"Type mismatch for '{key}': expected {expected_type_str}, got"
              f" {type(val).__name__}",
          )

    return True, None