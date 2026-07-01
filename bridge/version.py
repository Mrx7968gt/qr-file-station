"""采集卡文件传输发送端 · 版本号。

单处定义,被以下地方读取:
  - build/win/sender.spec  → exe 文件名带版本
  - .github/workflows      → artifact 名带版本
  - GUI 窗口标题            → 显示当前版本
"""
VERSION = "1.1.0"
