# SaveYourWindows

## 简介

**SaveYourWindows** 是一套用于拯救与维护 Windows 系统环境的实用脚本集合。通过清理垃圾、禁用拖慢系统的服务、以及提供便捷的 CMake 构建辅助，帮助用户恢复系统流畅度、释放磁盘空间，并简化开发环境配置。

---

## 脚本说明

### `clean_temp.py` — 清理临时文件与系统垃圾

以管理员身份运行，扫描并删除 Windows 中各类临时文件、缓存和无用数据，释放磁盘空间。

```bash
# 交互模式（推荐）
python clean_temp.py

# 仅预览，不实际删除
python clean_temp.py --dry-run

# 自动清理安全项（无需确认）
python clean_temp.py --safe-only

# 清理全部项（含条件项），跳过确认
python clean_temp.py --all --yes
```

- **安全项**：用户临时文件、系统 Temp、Prefetch、回收站、缩略图缓存、IE/Edge 缓存、Defender 扫描历史等，可放心清理。
- **条件项**：Windows Update 残留（DISM）、Windows.old、DirectX 着色器缓存、旧驱动包、内存转储、升级日志、系统还原点（保留最近一个）等，清理前请确认。

---

### `disable_services.py` — 禁用问题服务

以管理员身份运行，停止并禁用导致黑屏或开机缓慢的系统服务，同时自动备份原启动类型以便恢复。

```bash
# 交互模式（推荐）
python disable_services.py

# 预览变更
python disable_services.py --dry-run

# 自动应用，跳过确认
python disable_services.py --yes

# 查看目标服务当前状态
python disable_services.py --list

# 从备份恢复原始启动类型
python disable_services.py --restore
```

- **黑屏相关**：App Readiness、SysMain、Windows Search、诊断遥测等可能导致开机后仅显示鼠标黑屏。
- **慢启动相关**：Print Spooler、程序兼容性助手、远程桌面、Windows Connect Now 等拖慢启动速度。
- 备份文件：`disable_services.backup.json`

---

### `windows_cmake.py` — Windows CMake 构建助手

自动探测 CMake、Ninja 与 MSVC 环境（通过 vswhere），无需打开 Visual Studio 开发者命令提示即可直接配置和构建项目。

```bash
# 一键配置 + 构建
python windows_cmake.py -S <源码目录> -B <构建目录>

# 仅配置
python windows_cmake.py -S <src> -B <build> --config

# 仅构建
python windows_cmake.py -S <src> -B <build> --build-step

# 清理缓存后重新配置
python windows_cmake.py -S <src> -B <build> --clean

# 构建并验证输出文件
python windows_cmake.py -S <src> -B <build> --verify libfoo.dll

# Debug 模式 + 自定义参数
python windows_cmake.py -S <src> -B <build> --type Debug -DFOO=BAR
```

- 自动查找 `vcvars64.bat` 注入 MSVC 环境变量。
- 支持 Ninja 多核并行编译（默认使用 CPU 核心数）。

---

## 总结

| 脚本 | 作用 | 需管理员 |
|------|------|----------|
| `clean_temp.py` | 清理系统垃圾，释放磁盘空间 | ✅ |
| `disable_services.py` | 禁用问题服务，解决黑屏/慢启动 | ✅ |
| `windows_cmake.py` | 零配置 CMake 构建，自动检测 MSVC | ❌ |

SaveYourWindows 的目标是让 Windows 系统保持干净、快速、开发友好。按需求选择对应脚本运行即可。
