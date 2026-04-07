"""Tests for ONNXInferenceService."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mt5_mcp.services.onnx_inference import ONNXInferenceService


def _make_mock_session(
    input_shape=None, output_shape=None, input_name="X", output_name="Y"
):
    session = MagicMock()
    input_info = MagicMock()
    input_info.name = input_name
    input_info.shape = input_shape or [1, 5]
    input_info.type = "tensor(float)"

    output_info = MagicMock()
    output_info.name = output_name
    output_info.shape = output_shape or [1, 2]
    output_info.type = "tensor(float)"

    session.get_inputs.return_value = [input_info]
    session.get_outputs.return_value = [output_info]
    session.run.return_value = [np.array([[0.7, 0.3]], dtype=np.float32)]
    return session


class TestServiceInit:
    def test_init_with_empty_dir_no_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ONNXInferenceService(models_dir=tmpdir)
            assert svc._models == {}
            assert svc._sessions == {}

    def test_init_creates_models_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            models_dir = os.path.join(tmpdir, "nested", "models")
            svc = ONNXInferenceService(models_dir=models_dir)
            assert os.path.isdir(models_dir)

    def test_init_with_nonexistent_dir_no_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent = os.path.join(tmpdir, "does_not_exist_yet")
            svc = ONNXInferenceService(models_dir=nonexistent)
            assert os.path.isdir(nonexistent)
            assert svc._models == {}


class TestAutoDiscover:
    def test_discovers_onnx_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "model_a.onnx"), "w").close()
            open(os.path.join(tmpdir, "model_b.onnx"), "w").close()
            open(os.path.join(tmpdir, "readme.txt"), "w").close()

            svc = ONNXInferenceService(models_dir=tmpdir)
            assert "model_a" in svc._models
            assert "model_b" in svc._models
            assert "readme" not in svc._models

    def test_no_models_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_dir = os.path.join(tmpdir, "empty")
            svc = ONNXInferenceService(models_dir=empty_dir)
            assert svc._models == {}


class TestPredict:
    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_classification_returns_prediction_and_confidence(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 5], output_shape=[1, 2])
        mock_session.run.return_value = [np.array([[0.73, 0.27]], dtype=np.float32)]
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "test_model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)
            result = svc.predict("test_model", [1.0, 2.0, 3.0, 4.0, 5.0])

            assert result["model_name"] == "test_model"
            assert result["prediction"] == "up"
            assert result["confidence"] == pytest.approx(0.73, abs=0.01)
            assert isinstance(result["inference_time_ms"], float)
            assert result["inference_time_ms"] >= 0

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_regression_returns_raw_output(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 3], output_shape=[1, 1])
        mock_session.run.return_value = [np.array([[42.5]], dtype=np.float32)]
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "regressor.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)
            result = svc.predict("regressor", [1.0, 2.0, 3.0])

            assert result["prediction"] == pytest.approx(42.5, abs=0.01)
            assert result["confidence"] is None

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_rejects_unknown_model(self, mock_load):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ONNXInferenceService(models_dir=tmpdir)
            with pytest.raises(ValueError, match="not found"):
                svc.predict("nonexistent", [1.0, 2.0])

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_rejects_mismatched_input_shape(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 5], output_shape=[1, 2])
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)

            with pytest.raises(ValueError, match="expected 5 features, got 3"):
                svc.predict("model", [1.0, 2.0, 3.0])

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_model_info_includes_shapes(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 5], output_shape=[1, 2])
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)
            result = svc.predict("model", [1.0, 2.0, 3.0, 4.0, 5.0])

            assert result["model_info"]["input_shape"] == [1, 5]
            assert result["model_info"]["output_shape"] == [1, 2]
            assert "X" in result["model_info"]["input_names"]
            assert "Y" in result["model_info"]["output_names"]

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_softmax_when_outputs_dont_sum_to_one(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 3], output_shape=[1, 2])
        mock_session.run.return_value = [np.array([[5.0, 1.0]], dtype=np.float32)]
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)
            result = svc.predict("model", [1.0, 2.0, 3.0])

            raw = result["raw_output"]
            assert abs(sum(raw) - 1.0) < 0.01
            assert result["prediction"] == "up"
            assert result["confidence"] > 0.9

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_feature_names_validation(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 3], output_shape=[1, 1])
        mock_session.run.return_value = [np.array([[1.0]], dtype=np.float32)]
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)

            result = svc.predict(
                "model", [1.0, 2.0, 3.0], feature_names=["a", "b", "c"]
            )
            assert result["prediction"] == pytest.approx(1.0)

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_predict_feature_names_length_mismatch(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 3], output_shape=[1, 1])
        mock_session.run.return_value = [np.array([[1.0]], dtype=np.float32)]
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)

            with pytest.raises(ValueError, match="does not match"):
                svc.predict("model", [1.0, 2.0, 3.0], feature_names=["a", "b"])


class TestListModels:
    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_list_models_returns_metadata(self, mock_load):
        mock_session = _make_mock_session(input_shape=[1, 5], output_shape=[1, 2])
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "my_model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)
            result = svc.list_models()

            assert "my_model" in result["models"]
            model_info = result["models"]["my_model"]
            assert model_info["loaded"] is True
            assert model_info["input_shape"] == [1, 5]
            assert model_info["output_shape"] == [1, 2]
            assert ".onnx" in model_info["file"]

    def test_list_models_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ONNXInferenceService(models_dir=tmpdir)
            result = svc.list_models()
            assert result["models"] == {}


class TestReload:
    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_reload_loads_new_models(self, mock_load):
        mock_session = _make_mock_session()
        mock_load.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ONNXInferenceService(models_dir=tmpdir)
            open(os.path.join(tmpdir, "new_model.onnx"), "w").close()

            result = svc.reload()
            assert result["loaded"] == 1
            assert result["failed"] == []
            assert "new_model" in svc._sessions

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_reload_handles_corrupt_file_gracefully(self, mock_load):
        mock_load.side_effect = Exception("corrupt model file")

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "bad_model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)

            result = svc.reload()
            assert result["loaded"] == 0
            assert "bad_model" in result["failed"]

    @patch("mt5_mcp.services.onnx_inference.ONNXInferenceService._load_model")
    def test_reload_mixed_valid_and_invalid(self, mock_load):
        mock_session = _make_mock_session()

        def side_effect(name):
            if name == "good_model":
                return mock_session
            raise Exception("corrupt")

        mock_load.side_effect = side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "good_model.onnx"), "w").close()
            open(os.path.join(tmpdir, "bad_model.onnx"), "w").close()
            svc = ONNXInferenceService(models_dir=tmpdir)

            result = svc.reload()
            assert result["loaded"] == 1
            assert "bad_model" in result["failed"]
