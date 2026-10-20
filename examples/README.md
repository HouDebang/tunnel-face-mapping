# 输入样例

仓库追踪 `synthetic_face.png` 和对应 JSON，二者是程序生成的测试图，仅验证执行与计算，不代表真实照片识别精度。

重新生成：

```powershell
.\.venv\Scripts\python.exe tools/generate_synthetic.py --output examples/synthetic_face.png
```

真实工程照片和来源记录继续保留在本地，默认不进入 Git。克隆代码后可自行放入 JPG/PNG 并通过 CLI 或工作台上传分析。
