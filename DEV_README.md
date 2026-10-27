# Mimora v2 开发环境

## 快速启动

```powershell
# 启动应用（默认）
.\dev.ps1

# 运行单元测试
.\dev.ps1 -m unittest tests.test_stats tests.test_analysis -v

# 运行冒烟测试
.\dev.ps1 _smoke_logic.py
```

## 环境说明

| 组件 | 路径 |
|------|------|
| Python 环境 | `C:\Users\abobot\AppData\Roaming\uv\tools\mimora` |
| 源代码 | `.\mimora\` (editable install) |
| 用户数据 | `C:\Users\abobot\AppData\Roaming\Mimora` (MIMORA_HOME) |
| 模型缓存 | `C:\Users\abobot\AppData\Roaming\Mimora\model_cache` |
| GGUF 模型 | `C:\Users\abobot\AppData\Roaming\Mimora\models` |

## 已安装依赖

- Python 3.12.0
- torch 2.14.1+cpu
- transformers 5.20.2
- kokoro 0.9.4
- spacy 3.8.16
- librosa 1.0.0
- sounddevice 0.5.6
- ttkbootstrap (present)
- 136 packages total

## 关键命令

```powershell
# 重新安装开发版本（修改代码后通常不需要，editable mode 自动生效）
uv pip install --python C:\Users\abobot\AppData\Roaming\uv\tools\mimora\Scripts\python.exe --editable .

# 运行所有测试
.\dev.ps1 -m unittest discover -s tests -v

# 运行特定测试
.\dev.ps1 -m unittest tests.test_stats -v
.\dev.ps1 -m unittest tests.test_analysis -v
.\dev.ps1 -m unittest tests.test_drill -v

# 查看 stats.json
Get-Content "C:\Users\abobot\AppData\Roaming\Mimora\config\stats.json" -Raw | ConvertFrom-Json

# 查看日志
Get-Content "C:\Users\abobot\AppData\Roaming\Mimora\logs\main.log" -Tail 50
```

## 新增功能模块

| 模块 | 功能 |
|------|------|
| `mimora/persistence/` | 持久化存储（stats.json、音频文件） |
| `mimora/analysis/` | 错误词聚合、音素分析、建议引擎 |
| `mimora/drill/` | Drill 练习引擎 |
| `mimora/ui_history_browse.py` | 历史浏览界面 |
| `mimora/ui_stats.py` | 统计面板 |
| `mimora/ui_weakness.py` | 薄弱项分析 |
| `mimora/ui_drill.py` | Drill 模式界面 |

## 主界面按钮

主窗口右上角新增 5 个按钮（从左到右）：

| 按钮 | 功能 |
|------|------|
| 📚 | 历史浏览 |
| 📊 | 统计面板 |
| 🎯 | 薄弱项分析 |
| ▶ | Drill 练习 |
| ⚙ | 设置（原有） |

## 测试覆盖率

- 单元测试：93 个用例全部通过
- 冒烟测试：8 个场景全部通过
- UI 导入：5 个模块全部导入成功
