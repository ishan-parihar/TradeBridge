from __future__ import annotations

from typing import Any, Optional

from mcp.types import ToolAnnotations

from . import mcp

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)

_onnx_service = None


def _get_onnx_service():
    global _onnx_service
    if _onnx_service is not None:
        return _onnx_service
    try:
        from mt5_mcp.services.onnx_inference import ONNXInferenceService

        _onnx_service = ONNXInferenceService()
    except ImportError:
        return None
    except Exception:
        return None
    return _onnx_service


@mcp.tool(name="mt5_ml_predict", annotations=_READ_ANNOTATIONS)
def mt5_ml_predict(
    model_name: str, features: list[float], feature_names: Optional[list[str]] = None
) -> dict:
    try:
        svc = _get_onnx_service()
        if svc is None:
            return {"error": "ONNX runtime not available. Install onnxruntime package."}
        return svc.predict(model_name, features, feature_names)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_ml_models", annotations=_READ_ANNOTATIONS)
def mt5_ml_models() -> dict:
    try:
        svc = _get_onnx_service()
        if svc is None:
            return {
                "models": {},
                "status": "unavailable",
                "hint": (
                    "No ML models loaded. To enable ML predictions: "
                    "1. Install onnxruntime: pip install onnxruntime "
                    "2. Place .onnx model files in the models/ directory "
                    "3. Call mt5_ml_models_reload to scan and load models. "
                    "Trading can proceed without ML — use technical analysis instead."
                ),
            }
        models = svc.list_models()
        if not models or not models.get("models"):
            return {
                "models": {},
                "status": "no_models_loaded",
                "hint": (
                    "ONNX runtime is available but no models found. "
                    "Place .onnx model files in the models/ directory and call mt5_ml_models_reload."
                ),
            }
        return models
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(name="mt5_ml_models_reload", annotations=_WRITE_ANNOTATIONS)
def mt5_ml_models_reload() -> dict:
    try:
        svc = _get_onnx_service()
        if svc is None:
            return {"error": "ONNX runtime not available"}
        return svc.reload()
    except Exception as e:
        return {"error": str(e)}
