# Hermes - AI Agent 编排系统

[Python]: https://www.python.org/downloads/

## 开发

```bash
# 安装依赖
pip install pytest

# 运行测试（需要设置 PYTHONPATH）
PYTHONPATH=src pytest tests/ -v

# 启动看板
PYTHONPATH=src python -m hermes.dashboard --port 8420
```

## 测试

所有测试使用 `unittest.mock` 模拟 Claude Code/Codex，不需要真实的 Agent 运行。

```bash
PYTHONPATH=src pytest tests/ -v
```
