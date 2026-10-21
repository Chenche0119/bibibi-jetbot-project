"""P7: export a trained YOLOv8 weight to ONNX and print the Jetson Nano trtexec command.

Usage: python3 -I export_onnx.py <best.pt> [imgsz] [opset]

Defaults: imgsz 320, opset 12 (conservative for TensorRT 8.2 on Nano; confirm on device).
Static shape, simplified graph. The printed trtexec line builds an FP16 engine on the Nano.
"""
import os, sys

weights = sys.argv[1]
imgsz = int(sys.argv[2]) if len(sys.argv) > 2 else 320
opset = int(sys.argv[3]) if len(sys.argv) > 3 else 12

from ultralytics import YOLO

onnx_path = YOLO(weights).export(format="onnx", imgsz=imgsz, opset=opset, simplify=True, dynamic=False)
name = os.path.splitext(os.path.basename(onnx_path))[0]
print(f"\nONNX: {onnx_path}\nOn the Nano:\n"
      f"/usr/src/tensorrt/bin/trtexec --onnx={os.path.basename(onnx_path)} "
      f"--saveEngine={name}_fp16.engine --fp16 --workspace=1024")
