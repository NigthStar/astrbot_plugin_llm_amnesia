# 🧠 AstrBot 遗忘插件

[![AstrBot Plugin](https://img.shields.io/badge/AstrBot-Plugin-blue.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 🎯 功能简介

当不满意LLM的回复时，可以使用指令从AstrBot的上下文管理器中删除指定数量的对话轮次，让大模型"遗忘"这些对话，然后用户重新发送问题，让LLM重新回复。

### ✨ 核心特性

- **🔮 智能遗忘**: 支持删除1-10轮对话，精准控制遗忘范围
- **↩️ 反悔功能**: 支持取消遗忘操作，恢复被删除的对话
- **⏰ 自动清理**: 自动清理过期的删除记录，防止内存泄漏
- **📊 状态查询**: 随时查看遗忘状态和可恢复的对话详情
- **🔒 会话隔离**: 按会话和用户隔离，互不影响
- **📚 帮助系统**: 完整的使用帮助和指令说明

## 🚀 安装方法

### 方法一：通过插件市场安装（推荐）

1. 打开 AstrBot WebUI
2. 进入"插件管理"页面
3. 在插件市场中搜索"遗忘插件"
4. 点击安装即可

### 方法二：手动安装

1. 克隆本仓库到 AstrBot 插件目录
```bash
cd AstrBot/data/plugins
git clone https://github.com/your-username/astrbot_plugin_forget.git
```

2. 重启 AstrBot 或在 WebUI 中重载插件

## 📖 使用说明

### 指令列表

| 指令 | 描述 | 示例 |
|------|------|------|
| `/forget` | 遗忘最新1轮对话 | `/forget` |
| `/forget [n]` | 遗忘最新n轮对话 | `/forget 3` |
| `/cancel_forget` | 取消遗忘，恢复对话 | `/cancel_forget` |
| `/forget_status` | 查看遗忘状态 | `/forget_status` |
| `/forget_help` | 显示帮助信息 | `/forget_help` |

### 使用场景

#### 场景1：不满意AI回复时重新生成

当你对AI的回复不满意时，可以使用遗忘功能让AI重新回答：

```
用户: 帮我写一个Python函数来计算斐波那契数列
AI: def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)
用户: /forget  ← 不满意这个递归实现，想要更高效的版本
AI: ✅ 已遗忘最新一轮对话
被删除的对话:
👤 你: 帮我写一个Python函数来计算斐波那契数列
🤖 AI: def fibonacci(n):...
💡 在下一条消息发送前，发送 /cancel_forget 可以恢复这段对话
用户: 帮我写一个Python函数来计算斐波那契数列
AI: def fibonacci(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n+1):
        a, b = b, a + b
    return b  ← 获得了更高效的迭代实现
```

#### 场景2：批量遗忘多轮对话

当你有多轮不满意的对话时，可以批量删除：

```
# 假设有以下对话历史：
# 轮次1: 你好 -> 你好！有什么可以帮助你的吗？
# 轮次2: 写首诗 -> 春风拂面花自开...
# 轮次3: 再写一首 -> 雨夜窗前思故乡...
# 轮次4: 写首词 -> 明月几时有...

用户: /forget 3
AI: ✅ 已遗忘 3 轮对话

删除了 3 轮对话:

轮次1:
👤 你: 写首诗...
🤖 AI: 春风拂面花自开，柳绿桃红映山崖。

轮次2:
👤 你: 再写一首...
🤖 AI: 雨夜窗前思故乡，灯火阑珊夜未央。

轮次3:
👤 你: 写首词...
🤖 AI: 明月几时有，把酒问青天。

💡 在下一条消息发送前，发送 /cancel_forget 可以恢复这些对话
```

#### 场景3：使用反悔功能

不小心删除了重要对话，可以立即恢复：

```
用户: 帮我总结这篇文章的主要内容
AI: [详细的总结内容...]
用户: /forget  ← 误操作，不小心删除了
AI: ✅ 已遗忘最新一轮对话
用户: /cancel_forget  ← 立即反悔，恢复对话
AI: ✅ 已恢复被删除的对话
恢复的对话:
👤 你: 帮我总结这篇文章的主要内容
🤖 AI: [详细的总结内容...]
对话已恢复到之前的状态
```

#### 场景4：查看遗忘状态

随时查看当前的遗忘状态：

```
用户: /forget_status
AI: 📝 遗忘状态

你有可恢复的遗忘记录:
⏰ 删除时间: 5分钟前
🔄 删除轮次: 3轮
💬 删除消息数: 6条

💡 发送 /cancel_forget 可以恢复这些对话
```

## 🔧 技术实现

### 核心原理

基于AstrBot官方文档推荐的对话管理方式：

```python
# 正确的获取方式
unified_msg_origin = event.unified_msg_origin
conv_mgr = self.context.conversation_manager
curr_cid = await conv_mgr.get_curr_conversation_id(unified_msg_origin)
conversation = await conv_mgr.get_conversation(unified_msg_origin, curr_cid)
```

### 批量删除算法

使用智能算法查找和删除多轮对话：

```python
def find_conversation_rounds(self, conversation_history: List[dict], round_count: int):
    """从后往前查找指定数量的用户-助手对话轮次"""
    # 智能查找算法，确保删除正确的对话轮次
```

## 🛠️ 配置选项

### 限制设置
- **最大遗忘轮次**: 10轮
- **自动清理时间**: 30分钟
- **反悔时间限制**: 下一条消息发送前

### 安全机制
- 会话和用户完全隔离
- 自动错误处理和异常捕获
- 内存泄漏防护

## 📚 开发信息

### 插件结构
```
astrbot_plugin_forget/
├── main.py              # 主要插件代码
├── metadata.yaml        # 插件元数据
├── requirements.txt     # 依赖库
├── README.md           # 使用说明
├── examples.md         # 使用示例
├── install.py          # 安装脚本
└── test_*.py           # 测试脚本
```

### 依赖要求
- AstrBot v3.0+
- Python 3.8+
- 无额外第三方依赖

## 📝 更新日志

### v1.1.0 (2025-11-12)
- ✅ 新增批量遗忘功能，支持1-10轮对话
- ✅ 新增 `/forget_help` 帮助指令
- ✅ 增强 `/forget_status` 状态显示
- ✅ 优化用户反馈信息
- ✅ 完善错误处理机制

### v1.0.0 (2025-11-12)
- ✅ 基础遗忘功能
- ✅ 反悔功能
- ✅ 状态查询功能
- ✅ 自动清理机制
- ✅ 会话用户隔离

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发环境搭建
1. Fork 本仓库
2. 克隆到本地
3. 在 AstrBot 插件目录中创建符号链接
4. 开始开发

### 代码规范
- 使用类型注解
- 添加充分的注释
- 遵循 PEP 8 规范
- 添加异常处理

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🔗 相关链接

- [AstrBot 官方文档](https://docs.astrbot.app/)
- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin.html)
- [AstrBot 社区](https://github.com/AstrBotDevs/AstrBot)

## 💬 联系方式

如有问题或建议，欢迎：
- 提交 [Issue](https://github.com/your-username/astrbot_plugin_forget/issues)
- 加入 AstrBot 官方交流群
- 发送邮件至: 1053757925@qq.com

---

⭐ 如果这个项目对你有帮助，请给个 Star 支持一下！

**享受你的遗忘插件吧！** 🧠✨
