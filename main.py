from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest
import asyncio
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import json

@register(
    "llm_amnesia",
    "NigthStar",
    "当您不满意大模型的回复时，使用 /forget 指令，让它“忘记”最近的N轮对话，以便您重新提问并获得更好的回答。",
    "1.1.5",
    "https://github.com/NigthStar/astrbot_plugin_llm_amnesia"
)
class ForgetPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 临时存储被删除的对话，用于反悔功能
        self.deleted_conversations: Dict[str, Dict[str, Tuple[List[dict], str, datetime, int]]] = {}
        # 为并发访问 self.deleted_conversations 创建一个锁
        self.lock = asyncio.Lock()
        # 后台清理任务
        self.cleanup_task = asyncio.create_task(self.initialize_cleanup_task())
        logger.info("遗忘插件已加载并启动清理任务")

    async def initialize_cleanup_task(self):
        """初始化定期清理任务"""
        while True:
            try:
                await self.cleanup_expired_deletions()
                await asyncio.sleep(300)  # 每5分钟清理一次
            except asyncio.CancelledError:
                logger.info("清理任务被取消")
                break
            except Exception as e:
                logger.error(f"清理任务出错: {e}")
                await asyncio.sleep(60)

    async def cleanup_expired_deletions(self):
        """(并发安全) 清理过期的删除记录"""
        async with self.lock:
            current_time = datetime.now()
            # 遍历会话的副本，以便在循环中安全地删除元素
            for unified_msg_origin, user_deletions in list(self.deleted_conversations.items()):
                # 遍历用户的副本
                for user_id, (_, _, timestamp, _) in list(user_deletions.items()):
                    if current_time - timestamp > timedelta(minutes=30):
                        del user_deletions[user_id]
                        logger.info(f"清理过期删除记录: unified_msg_origin={unified_msg_origin}, user={user_id}")
                
                # 如果清理后该会话下已无记录，则删除该会话键
                if not user_deletions:
                    del self.deleted_conversations[unified_msg_origin]

    @filter.on_llm_request()
    async def on_llm_request_cleanup(self, event: AstrMessageEvent, req: ProviderRequest):
        """(并发安全) 在AstrBot即将调用LLM前，自动清除可“反悔”的遗忘记录"""
        unified_msg_origin = event.unified_msg_origin
        user_id = event.get_sender_id()
        
        async with self.lock:
            if (unified_msg_origin in self.deleted_conversations and 
                user_id in self.deleted_conversations[unified_msg_origin]):
                
                del self.deleted_conversations[unified_msg_origin][user_id]
                if not self.deleted_conversations[unified_msg_origin]:
                    del self.deleted_conversations[unified_msg_origin]
                
                logger.info(f"用户 {user_id} 发起了新的LLM请求，自动清除遗忘记录。")

    @filter.command("forget")
    async def forget_conversations(self, event: AstrMessageEvent, round_count: int = 1):
        """(并发安全) 遗忘指定数量的对话轮次"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            logger.info(f"forget指令开始 - 会话: {unified_msg_origin}, 用户: {user_id}, 轮次: {round_count}")

            if not 1 <= round_count <= 10:
                yield event.plain_result("遗忘轮次数必须在1到10之间 ❌")
                return

            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(unified_msg_origin)
            if not curr_cid:
                logger.warning("无法获取当前对话ID")
                yield event.plain_result("无法获取当前对话ID ❌")
                return
            
            logger.info(f"获取到当前对话ID: {curr_cid}")
            
            conversation = await conv_mgr.get_conversation(unified_msg_origin, curr_cid)
            if not conversation:
                logger.warning("无法获取对话对象")
                yield event.plain_result("无法获取对话对象 ❌")
                return

            logger.info(f"成功获取对话对象，对话ID: {conversation.cid}")
            logger.info(f"对话历史长度: {len(conversation.history) if conversation.history else 0}")

            conversation_history = []
            try:
                conversation_history = json.loads(conversation.history) if conversation.history else []
                logger.info(f"对话历史解析成功，长度: {len(conversation_history)}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析错误: {e}")
                yield event.plain_result("对话历史格式错误 ❌")
                return

            if len(conversation_history) < round_count * 2:
                yield event.plain_result(f"对话历史不足 {round_count} 轮，当前只有 {len(conversation_history)//2} 轮对话 ❌")
                return
            
            split_index = len(conversation_history)
            rounds_found = 0
            for i in range(len(conversation_history) - 1, 0, -2):
                if conversation_history[i].get("role") == "assistant" and conversation_history[i-1].get("role") == "user":
                    rounds_found += 1
                    if rounds_found == round_count:
                        split_index = i - 1
                        break
            
            if split_index == len(conversation_history):
                logger.info(f"没有找到足够的可删除对话轮次")
                yield event.plain_result(f"只找到了 {rounds_found} 轮可遗忘的对话 ❌")
                return
            
            new_conversation_history = conversation_history[:split_index]
            deleted_messages = conversation_history[split_index:]

            async with self.lock:
                if unified_msg_origin not in self.deleted_conversations:
                    self.deleted_conversations[unified_msg_origin] = {}
                self.deleted_conversations[unified_msg_origin][user_id] = (
                    deleted_messages, conversation.cid, datetime.now(), round_count
                )

            await conv_mgr.update_conversation(
                unified_msg_origin, conversation.cid, history=new_conversation_history
            )
            
            logger.info(f"删除对话: 原始长度 {len(conversation_history)} -> 新长度 {len(new_conversation_history)}")
            logger.info(f"用户 {user_id} 在会话 {unified_msg_origin} 遗忘了 {round_count} 轮对话")
            
            deleted_info = f"删除了 {round_count} 轮对话:\n\n"
            for i in range(0, len(deleted_messages), 2):
                user_msg = deleted_messages[i].get('content', '')[:50]
                assistant_msg = deleted_messages[i+1].get('content', '')[:50]
                deleted_info += f"轮次{i//2 + 1}:\n"
                deleted_info += f"👤 你: {user_msg}{'...' if len(user_msg) >= 50 else ''}\n"
                deleted_info += f"🤖 AI: {assistant_msg}{'...' if len(assistant_msg) >= 50 else ''}\n\n"
            
            yield event.plain_result(
                f"✅ 已遗忘 {round_count} 轮对话\n\n{deleted_info}💡 在下一条消息发送前，发送 /cancel_forget 可以恢复这些对话"
            )
            
        except Exception as e:
            logger.error(f"遗忘对话时出错: {e}")
            import traceback
            logger.error(f"堆栈跟踪: {traceback.format_exc()}")
            yield event.plain_result(f"遗忘对话时出现错误 ❌: {str(e)}")

    @filter.command("cancel_forget")
    async def cancel_forget(self, event: AstrMessageEvent):
        """(并发安全) 取消遗忘操作，恢复被删除的对话"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            record_to_restore = None
            async with self.lock:
                if (unified_msg_origin in self.deleted_conversations and 
                    user_id in self.deleted_conversations[unified_msg_origin]):
                    record_to_restore = self.deleted_conversations[unified_msg_origin].pop(user_id)
                    if not self.deleted_conversations[unified_msg_origin]:
                        self.deleted_conversations.pop(unified_msg_origin)

            if not record_to_restore:
                yield event.plain_result("没有可恢复的遗忘记录 ❌")
                return
            
            deleted_messages, conversation_id, _, round_count = record_to_restore
            
            conv_mgr = self.context.conversation_manager
            conversation = await conv_mgr.get_conversation(unified_msg_origin, conversation_id)
            
            if conversation is None:
                yield event.plain_result("获取当前对话失败 ❌")
                return
            
            conversation_history = json.loads(conversation.history) if conversation.history else []
            restored_conversation_history = conversation_history + deleted_messages
            
            await conv_mgr.update_conversation(
                unified_msg_origin, conversation_id, history=restored_conversation_history
            )
            
            logger.info(f"用户 {user_id} 在会话 {unified_msg_origin} 取消了遗忘操作，恢复了 {round_count} 轮对话")
            
            yield event.plain_result(
                f"✅ 已恢复 {round_count} 轮被删除的对话\n\n对话已恢复到之前的状态"
            )
            
        except Exception as e:
            logger.error(f"取消遗忘时出错: {e}")
            yield event.plain_result(f"恢复对话时出现错误 ❌: {str(e)}")

    @filter.command("forget_status")
    async def forget_status(self, event: AstrMessageEvent):
        """(并发安全) 查看遗忘状态"""
        try:
            unified_msg_origin = event.unified_msg_origin
            user_id = event.get_sender_id()
            
            async with self.lock:
                record = self.deleted_conversations.get(unified_msg_origin, {}).get(user_id)
                if record:
                    deleted_messages, _, timestamp, round_count = record
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
        """
        yield event.plain_result(help_text.strip())

    async def terminate(self):
        """插件卸载时的清理工作"""
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass  # 任务取消是正常行为
            logger.info("清理任务已取消")
        logger.info("遗忘插件已卸载")
