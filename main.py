from astrbot.api.all import *
import re
import aiohttp
import json
import os
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent

@register("nodetest", "Jason.Joestar", "一个伪造转发消息的插件", "1.0.0", "插件仓库URL")
class NodeTestPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.debug("伪造转发消息插件已初始化")
    
    async def get_qq_nickname(self, qq_number):
        """获取QQ昵称"""
        url = f"http://api.mmp.cc/api/qqname?qq={qq_number}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        logger.debug(f"QQ昵称API返回: {data}")
                        
                        if data.get("code") == 200 and "data" in data and "name" in data["data"]:
                            nickname = data["data"]["name"]
                            logger.debug(f"成功提取昵称: {nickname}")
                            if nickname:
                                return nickname
                    except Exception as e:
                        logger.debug(f"解析昵称出错: {str(e)}")
        
        return f"用户{qq_number}"
    
    async def parse_message_components(self, message_obj):
        """按顺序解析消息组件，将图片正确分配到对应的消息段"""
        segments = []
        current_segment = {"text": "", "images": []}
        segment_started = False
        
        try:
            prefix_skipped = False
            
            if hasattr(message_obj, 'message'):
                for comp in message_obj.message:
                    if isinstance(comp, Plain):
                        text = comp.text
                        
                        # 查找并去除前缀
                        if not prefix_skipped:
                            for prefix in ["/fake_msg", "fake_msg", "伪造消息"]:
                                if prefix in text:
                                    prefix_pos = text.find(prefix)
                                    text = text[prefix_pos + len(prefix):].lstrip()
                                    prefix_skipped = True
                                    break
                        
                        if "|" in text:
                            parts = text.split("|")
                            
                            current_segment["text"] += parts[0]
                            segment_started = True
                            
                            if current_segment["text"].strip():
                                segments.append(current_segment)
                            
                            for i in range(1, len(parts)-1):
                                segments.append({"text": parts[i], "images": []})
                            
                            if len(parts) > 1:
                                current_segment = {"text": parts[-1], "images": []}
                                segment_started = True
                        else:
                            current_segment["text"] += text
                            segment_started = True
                    
                    elif isinstance(comp, Image) and hasattr(comp, 'url') and comp.url:
                        if segment_started:
                            current_segment["images"].append(comp.url)
                            logger.debug(f"将图片 {comp.url} 添加到当前段落")
                
                if current_segment["text"].strip():
                    segments.append(current_segment)
            
            logger.debug(f"解析完成，共有 {len(segments)} 个段落")
            
            for i, seg in enumerate(segments):
                img_count = len(seg["images"])
                logger.debug(f"段落 {i+1}: 文本长度={len(seg['text'])}, 图片数量={img_count}")
                if img_count > 0:
                    logger.debug(f"段落 {i+1} 包含的图片: {seg['images']}")
        
        except Exception as e:
            logger.error(f"解析消息组件出错: {str(e)}")
            segments = []
        
        return segments

    async def parse_content_item(self, item) -> list:
        """
        递归解析单个 content 项或 content 数组为 BaseMessageComponent 列表。
        """
        from astrbot.api.message_components import Plain, Image as CompImage
        
        components = []
        if isinstance(item, str):
            components.append(Plain(item))
        elif isinstance(item, list):
            # 判断这个列表里是子转发节点还是普通组件
            # 如果列表里的每一项都是 dict 且包含 'uin' 或 'qq'，那它就是一个嵌套的转发消息 (Nodes)
            is_sub_nodes = len(item) > 0 and all(isinstance(x, dict) and ('uin' in x or 'qq' in x) for x in item)
            
            if is_sub_nodes:
                sub_nodes = await self.build_nodes_from_json_items(item)
                if sub_nodes:
                    components.extend(sub_nodes)
            else:
                for sub_item in item:
                    components.extend(await self.parse_content_item(sub_item))
        elif isinstance(item, dict):
            comp_type = item.get("type", "").lower()
            if comp_type in ["text", "plain"]:
                text_val = item.get("text") or item.get("value") or ""
                components.append(Plain(str(text_val)))
            elif comp_type in ["image", "img"]:
                img_url = item.get("url") or item.get("value") or ""
                if img_url:
                    try:
                        components.append(CompImage.fromURL(img_url))
                    except Exception as e:
                        logger.debug(f"解析 JSON 图片组件失败: {e}")
            elif comp_type in ["node", "nodes"]:
                data = item.get("data") or item.get("content") or []
                if isinstance(data, list):
                    sub_nodes = await self.build_nodes_from_json_items(data)
                    if sub_nodes:
                        components.extend(sub_nodes)
            else:
                if "uin" in item or "qq" in item:
                    sub_node_list = await self.build_nodes_from_json_items([item])
                    if sub_node_list:
                        components.extend(sub_node_list)
        return components

    async def build_nodes_from_json_items(self, json_items: list) -> list:
        """
        从解析后的 JSON 字典列表中构建 Node 对象列表 (支持递归嵌套)
        """
        from astrbot.api.message_components import Node, Plain, Image as CompImage
        
        nodes_list = []
        for item in json_items:
            uin = item.get("uin") or item.get("qq")
            if not uin:
                continue
            name = item.get("name") or item.get("nickname")
            if not name:
                name = await self.get_qq_nickname(str(uin))
                
            content_data = item.get("content") or item.get("text") or ""
            node_content = await self.parse_content_item(content_data)
            
            # 兼容旧有格式中的 images 字段
            images = item.get("images") or []
            if isinstance(images, str):
                images = [images]
            for img_url in images:
                try:
                    node_content.append(CompImage.fromURL(img_url))
                except Exception as e:
                    logger.debug(f"解析 JSON 独立 images 字段失败: {e}")
                    
            node = Node(
                uin=int(uin),
                name=str(name),
                content=node_content
            )
            nodes_list.append(node)
            
        return nodes_list

    async def build_nodes_from_input(self, text_content: str, message_obj=None) -> list:
        """
        根据输入文本和消息对象构建 Node 对象列表
        """
        from astrbot.api.message_components import Node, Plain, Image as CompImage
        
        text_content_stripped = text_content.strip()
        is_json = False
        json_data = None
        
        if (text_content_stripped.startswith("[") and text_content_stripped.endswith("]")) or \
           (text_content_stripped.startswith("{") and text_content_stripped.endswith("}")):
            try:
                json_data = json.loads(text_content_stripped)
                is_json = True
            except Exception as e:
                logger.debug(f"尝试解析JSON失败: {e}")
                
        if is_json:
            if isinstance(json_data, dict):
                json_items = [json_data]
            elif isinstance(json_data, list):
                json_items = json_data
            else:
                json_items = []
            return await self.build_nodes_from_json_items(json_items)
            
        # 旧有格式解析
        segments = []
        if message_obj:
            segments = await self.parse_message_components(message_obj)
            
        if not segments:
            text_segments = text_content.split('|')
            segments = [{"text": seg.strip(), "images": []} for seg in text_segments if seg.strip()]
            
        nodes_list = []
        for segment in segments:
            text = segment["text"]
            images = segment["images"]
            
            match = re.match(r'^\s*(\d+)\s+(.*)', text)
            if not match:
                logger.debug(f"段落格式错误，跳过: {text}")
                continue
                
            qq_number, content = match.group(1), match.group(2).strip()
            nickname = await self.get_qq_nickname(qq_number)
            
            node_content = [Plain(content)]
            for img_url in images:
                try:
                    node_content.append(CompImage.fromURL(img_url))
                except Exception as e:
                    logger.debug(f"添加图片到节点失败: {e}")
                    
            node = Node(
                uin=int(qq_number),
                name=nickname,
                content=node_content
            )
            nodes_list.append(node)
            
        return nodes_list

    @event_message_type(EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        '''监听所有消息并检测伪造消息请求'''
        message_text = event.message_str.strip()
        
        if not message_text.startswith("伪造消息"):
            return
            
        raw_message = message_text[len("伪造消息"):].lstrip()
        
        nodes_list = await self.build_nodes_from_input(raw_message, event.message_obj)
        
        if nodes_list:
            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.core.platform.message_session import MessageSession
            from astrbot.api.message_components import Nodes
            
            session = MessageSession.from_str(event.unified_msg_origin)
            await self.context.send_message(session, MessageChain(chain=[Nodes(nodes=nodes_list)]))
            event.stop_event()
        else:
            return event.plain_result("未能解析出任何有效的消息节点")

    @filter.command("fake_msg")
    async def fake_msg_command(self, event: AstrMessageEvent):
        """伪造转发消息"""
        raw_message = event.message_str.strip()
        for prefix in ["/fake_msg", "fake_msg"]:
            if raw_message.startswith(prefix):
                raw_message = raw_message[len(prefix):].lstrip()
                break
                
        nodes_list = await self.build_nodes_from_input(raw_message, event.message_obj)
        
        if nodes_list:
            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.core.platform.message_session import MessageSession
            from astrbot.api.message_components import Nodes
            
            session = MessageSession.from_str(event.unified_msg_origin)
            await self.context.send_message(session, MessageChain(chain=[Nodes(nodes=nodes_list)]))
        else:
            return event.plain_result("未能解析出任何有效的消息节点")

    @filter.llm_tool(name="fake_msg")
    async def fake_msg_tool(self, event: AstrMessageEvent, message: str):
        """
        伪造并发送转发消息给当前会话。支持普通文本和嵌套的合并转发卡片。

        Args:
            message(string): 伪造消息的内容。
                - 格式A（普通纯文本）：QQ号 消息内容 | QQ号 消息内容。
                - 格式B（嵌套JSON，推荐）：传入一个 JSON 数组。数组中的每个元素代表一条消息气泡，其字典字段包括：
                  * uin (number/string, 必填): 发送者 QQ。
                  * name (string, 可选): 发送者昵称（省略时自动获取）。
                  * content (string/array, 必填): 消息内容。若需要嵌套另一层合并转发卡片，应将 content 设为子节点数组。例如：
                    [{"uin": 12345, "name": "张三", "content": [{"uin": 67890, "name": "李四", "content": "这是嵌套的消息"}]}]
                  * images (array of string, 可选): 图片 URL 列表。
        """
        raw_message = message.strip()
        for prefix in ["/fake_msg", "fake_msg"]:
            if raw_message.startswith(prefix):
                raw_message = raw_message[len(prefix):].lstrip()
                break
                
        nodes_list = await self.build_nodes_from_input(raw_message)
        
        if nodes_list:
            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.core.platform.message_session import MessageSession
            from astrbot.api.message_components import Nodes
            
            session = MessageSession.from_str(event.unified_msg_origin)
            await self.context.send_message(session, MessageChain(chain=[Nodes(nodes=nodes_list)]))
            return "已成功发送伪造的转发消息。"
        else:
            return "构建转发消息失败，未能解析出任何有效的消息节点。"

    @filter.command("伪造帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示插件帮助信息"""
        help_text = """📱 伪造转发消息插件使用说明 📱

【基本格式】
- 伪造消息 QQ号 消息内容 | QQ号 消息内容 | ...
- /fake_msg QQ号 消息内容 | QQ号 消息内容 | ...

【JSON 格式】
- /fake_msg [JSON内容]
例如: /fake_msg [{"uin": 123456, "name": "张三", "content": "你好", "images": ["图片地址"]}]
其中 name 和 images 字段是可选的。如果 name 不填，将自动获取 QQ 昵称。

【嵌套 JSON 格式】
- content 字段里可以嵌套子消息数组，例如：
/fake_msg [{"uin": 123456, "name": "张三", "content": [{"uin": 111111, "name": "李四", "content": "嵌套消息"}]}]

【带图片的格式（普通格式）】
- 在任意消息段中添加图片，图片将只出现在它所在的消息段
- 例如: /fake_msg 123456 看我的照片[图片] | 654321 好漂亮啊

【注意事项】
- 普通格式 of 每个消息段之间用"|"分隔
- 每个消息段的格式必须是"QQ号 消息内容"
- 图片会根据它在消息中的位置分配到对应的消息段
"""
        return event.plain_result(help_text)
            
    async def terminate(self):
        '''插件被卸载/停用时调用'''
        pass
