from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.conversation_mgr import Conversation
import asyncio
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
import json

@register(
    "llm_amnesia",
    "NigthStar",
    "当您不满意大模型的回复时，使用 /forget 指令，让它“忘记”最近的N轮对话，以便您重新提问并获得更好的回答。",
    "1.0.0",
    "https://github.com/NigthStar/astrbot_plugin_llm_amnesia"
)
class ForgetPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 临时存储被删除的对话，用于反悔功能
        # 结构: {unified_msg_origin: {user_id: (deleted_messages, conversation_id, timestamp, round_count)}}
        self.deleted_conversations: Dict[str, Dict[str, Tuple[List[dict], str, datetime, int]]] = {}
        # 清理任务
        self.cleanup_task = None
        logger.info("遗忘插件已加载")

    async def initialize_cleanup_task(self):
        """初始化定期清理任务"""
        while True:
            try:
                await self.cleanup_expired_deletions()
                await asyncio.sleep(300)  # 每5分钟清理一次
            except Exception as e:
                logger.error(f"清理任务出错: {e}")
                await asyncio.sleep(60)

    async def cleanup_expired_deletions(self):
        """清理过期的删除记录"""
        current_time = datetime.now()
        expired_sessions = []
        
        for unified_msg_origin, user_deletions in self.deleted_conversations.items():
            expired_users = []
            for user_id, (messages, conversation_id, timestamp, round_count) in user_deletions.items():
                # 如果删除时间超过30分钟，则清理
                if current_time - timestamp > timedelta(minutes=30):
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                del user_deletions[user_id]
                logger.info(f"清理过期删除记录: unified_msg_origin={unified_msg_origin}, user={user_id}")
            
            if not user_deletions:
                expired_sessions.append(unified_msg_origin)
        
        for unified_msg_origin in expired_sessions:
            del self.deleted_conversations[unified_msg_origin]

    def find_conversation_rounds(self, conversation_history: List[dict], round_count: int) -> Tuple[List[dict], List[int]]:
        """查找指定数量的对话轮次
        
        Args:
            conversation_history: 对话历史列表
            round_count: 要删除的对话轮次数
            
        Returns:
            (deleted_messages, deleted_positions) - 被删除的消息和位置列表
        """
        deleted_messages = []
        deleted_positions = []
        found_rounds = 0
        
        # 从后往前查找指定数量的对话轮次
        for i in range(len(conversation_history) - 1, 0, -1):
            if found_rounds >= round_count:
                break
                
            current_msg = conversation_history[i]
            prev_msg = conversation_history[i-1]
            
            if (current_msg.get("role") == "assistant" and 
                prev_msg.get("role") == "user"):
                # 找到一轮对话，添加到删除列表
                deleted_messages.insert(0, prev_msg)  # 插入到开头保持顺序
                deleted_messages.insert(0, current_msg)
                deleted_positions.insert(0, i-1)  # 记录起始位置
                deleted_positions.insert(0, i)    # 记录结束位置
                found_rounds += 1
        
        return deleted_messages, deleted_positions

    @filter.command("forget")
    async def forget_conversations(self, event: AstrMessageEvent, round_count: int = 1):
        """遗忘指定数量的对话轮次
        
        Args:
            round_count: 要遗忘的对话轮次数，默认为1
        """
        try:
            # 使用文档中推荐的正确方式获取unified_msg_origin
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            logger.info(f"forget指令开始 - 会话: {unified_msg_origin}, 用户: {user_id}, 轮次: {round_count}")
            
            # 验证参数
            if round_count <= 0:
                yield event.plain_result("遗忘轮次数必须大于0 ❌")
                return
            
            if round_count > 10:
                yield event.plain_result("一次最多只能遗忘10轮对话 ❌")
                return
            
            # 使用文档中推荐的正确方式获取对话
            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(unified_msg_origin)
            
            if not curr_cid:
                logger.warning("无法获取当前对话ID")
                yield event.plain_result("无法获取当前对话ID ❌")
                return
            
            logger.info(f"获取到当前对话ID: {curr_cid}")
            
            # 获取对话对象
            conversation = await conv_mgr.get_conversation(unified_msg_origin, curr_cid)
            
            if not conversation:
                logger.warning("无法获取对话对象")
                yield event.plain_result("无法获取对话对象 ❌")
                return
            
            logger.info(f"成功获取对话对象，对话ID: {conversation.cid}")
            logger.info(f"对话历史长度: {len(conversation.history) if conversation.history else 0}")
            
            # 解析对话历史
            conversation_history = []
            
            try:
                if conversation.history:
                    conversation_history = json.loads(conversation.history)
                    logger.info(f"对话历史解析成功，长度: {len(conversation_history)}")
                else:
                    logger.info("对话历史为空")
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析错误: {e}")
                yield event.plain_result("对话历史格式错误 ❌")
                return
            
            if len(conversation_history) < round_count * 2:
                logger.info(f"对话历史不足 {round_count} 轮: 当前有 {len(conversation_history)//2} 轮")
                yield event.plain_result(f"对话历史不足 {round_count} 轮，当前只有 {len(conversation_history)//2} 轮对话 ❌")
                return
            
            # 查找指定数量的对话轮次
            deleted_messages, deleted_positions = self.find_conversation_rounds(conversation_history, round_count)
            
            if not deleted_messages:
                logger.info("没有找到可删除的对话轮次")
                yield event.plain_result("没有找到可遗忘的对话轮次 ❌")
                return
            
            # 保存被删除的对话用于反悔
            if unified_msg_origin not in self.deleted_conversations:
                self.deleted_conversations[unified_msg_origin] = {}
            
            self.deleted_conversations[unified_msg_origin][user_id] = (
                deleted_messages.copy(), 
                conversation.cid,
                datetime.now(),
                round_count
            )
            
            # 删除指定数量的对话轮次
            new_conversation_history = conversation_history.copy()
            
            # 按位置从后往前删除，避免索引变化问题
            for pos in sorted(deleted_positions, reverse=True):
                if pos < len(new_conversation_history):
                    del new_conversation_history[pos]
            
            logger.info(f"删除对话: 原始长度 {len(conversation_history)} -> 新长度 {len(new_conversation_history)}")
            logger.info(f"删除了 {round_count} 轮对话，共 {len(deleted_messages)} 条消息")
            
            # 更新对话历史
            await conv_mgr.update_conversation(
                unified_msg_origin, 
                conversation.cid,
                history=new_conversation_history
            )
            
            logger.info(f"用户 {user_id} 在会话 {unified_msg_origin} 遗忘了 {round_count} 轮对话")
            
            # 启动清理任务（如果还没启动）
            if self.cleanup_task is None or self.cleanup_task.done():
                self.cleanup_task = asyncio.create_task(self.initialize_cleanup_task())
            
            # 构建被删除对话的显示信息
            deleted_info = f"删除了 {round_count} 轮对话:\n\n"
            for i in range(0, len(deleted_messages), 2):
                if i + 1 < len(deleted_messages):
                    user_msg = deleted_messages[i].get('content', '')[:50]
                    assistant_msg = deleted_messages[i+1].get('content', '')[:50]
                    deleted_info += f"轮次{i//2 + 1}:\n"
                    deleted_info += f"👤 你: {user_msg}{'...' if len(user_msg) >= 50 else ''}\n"
                    deleted_info += f"🤖 AI: {assistant_msg}{'...' if len(assistant_msg) >= 50 else ''}\n\n"
            
            yield event.plain_result(
                f"✅ 已遗忘 {round_count} 轮对话\n\n"
                f"{deleted_info}"
                f"💡 在下一条消息发送前，发送 /cancel_forget 可以恢复这些对话"
            )
            
        except Exception as e:
            logger.error(f"遗忘对话时出错: {e}")
            import traceback
            logger.error(f"堆栈跟踪: {traceback.format_exc()}")
            yield event.plain_result(f"遗忘对话时出现错误 ❌: {str(e)}")

    @filter.command("cancel_forget")
    async def cancel_forget(self, event: AstrMessageEvent):
        """取消遗忘操作，恢复被删除的对话"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            # 检查是否有待恢复的删除记录
            if (unified_msg_origin not in self.deleted_conversations or 
                user_id not in self.deleted_conversations[unified_msg_origin]):
                yield event.plain_result("没有可恢复的遗忘记录 ❌")
                return
            
            # 获取被删除的对话
            deleted_messages, conversation_id, timestamp, round_count = self.deleted_conversations[unified_msg_origin][user_id]
            
            # 获取当前对话
            conv_mgr = self.context.conversation_manager
            conversation = await conv_mgr.get_conversation(unified_msg_origin, conversation_id)
            
            if conversation is None:
                yield event.plain_result("获取当前对话失败 ❌")
                return
            
            # 获取当前对话历史
            conversation_history = []
            if conversation.history:
                conversation_history = json.loads(conversation.history)
            
            # 恢复被删除的对话到对话末尾
            restored_conversation_history = conversation_history + deleted_messages
            
            # 更新对话
            await conv_mgr.update_conversation(
                unified_msg_origin, 
                conversation_id, 
                history=restored_conversation_history
            )
            
            # 清理删除记录
            del self.deleted_conversations[unified_msg_origin][user_id]
            
            # 如果该会话下没有其他用户的删除记录，清理会话记录
            if not self.deleted_conversations[unified_msg_origin]:
                del self.deleted_conversations[unified_msg_origin]
            
            logger.info(f"用户 {user_id} 在会话 {unified_msg_origin} 取消了遗忘操作，恢复了 {round_count} 轮对话")
            
            yield event.plain_result(
                f"✅ 已恢复 {round_count} 轮被删除的对话\n\n"
                f"恢复的对话已追加到对话历史末尾\n\n"
                f"对话已恢复到之前的状态"
            )
            
        except Exception as e:
            logger.error(f"取消遗忘时出错: {e}")
            yield event.plain_result(f"恢复对话时出现错误 ❌: {str(e)}")

    @filter.command("forget_status")
    async def forget_status(self, event: AstrMessageEvent):
        """查看遗忘状态"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            # 检查是否有待恢复的删除记录
            if (unified_msg_origin in self.deleted_conversations and 
                user_id in self.deleted_conversations[unified_msg_origin]):
                deleted_messages, conversation_id, timestamp, round_count = self.deleted_conversations[unified_msg_origin][user_id]
                time_ago = datetime.now() - timestamp
                minutes_ago = int(time_ago.total_seconds() / 60)
                
                yield event.plain_result(
                    f"📝 遗忘状态\n\n"
                    f"你有可恢复的遗忘记录:\n"
                    f"⏰ 删除时间: {minutes_ago}分钟前\n"
                    f"🔄 删除轮次: {round_count}轮\n"
                    f"💬 删除消息数: {len(deleted_messages)}条\n\n"
                    f"💡 发送 /cancel_forget 可以恢复这些对话"
                )
            else:
                yield event.plain_result("没有待恢复的遗忘记录 ✅")
                
        except Exception as e:
            logger.error(f"查看遗忘状态时出错: {e}")
            yield event.plain_result(f"查看状态时出错 ❌: {str(e)}")

    @filter.command("forget_help")
    async def forget_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
📝 遗忘插件使用帮助

📋 基本指令:
• /forget - 遗忘最新一轮对话
• /forget 3 - 遗忘最新3轮对话
• /forget 5 - 遗忘最新5轮对话

⚙️ 参数说明:
• 支持遗忘1-10轮对话
• 默认遗忘1轮对话

🔄 其他指令:
• /cancel_forget - 取消遗忘，恢复对话
• /forget_status - 查看遗忘状态
• /forget_help - 显示此帮助信息

⏰ 注意事项:
• 删除记录30分钟后自动清理
• 反悔功能只能在下一条消息发送前使用
• 一次最多只能遗忘10轮对话
        """
        yield event.plain_result(help_text.strip())

    async def terminate(self):
        """插件卸载时的清理工作"""
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
        logger.info("遗忘插件已卸载")