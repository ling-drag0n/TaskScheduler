from config import conf
from common.log import logger


class WrappedChannelTools:
    # itchat 的 UserName 其实都是 id
    def __init__(self):
        channel_type = conf().get("channel_type", "wx")
        if channel_type == "wx":
            from lib import itchat
            self.channel = itchat
            self.channel_type = "wx"
        elif channel_type == "ntchat":
            try:
                from channel.wechatnt.ntchat_channel import wechatnt
                self.channel = wechatnt
                self.channel_type = "ntchat"
            except Exception as e:
                logger.error(f"未安装ntchat: {e}")
        else:
            raise ValueError(f"不支持的channel_type: {channel_type}")
    
    def get_user_id_by_name(self, name):
        id = None
        if self.channel_type == "wx":
            friends =  self.channel.search_friends(name=name)
            if not friends:
                self.channel.get_friends(update=True)
                friends =  self.channel.search_friends(name=name)
            if not friends:
                return None
            return friends[0].get("UserName")

        elif self.channel_type == "ntchat":
            pass

        raise ValueError(f"不支持的channel_type: {self.channel_type}")
    

    # 根据群名称获取群ID
    def get_group_id_by_name(self, name:str):
        if self.channel_type == "wx":
            groups = self.channel.search_chatrooms(name=name)
            if not groups:
                self.channel.get_chatrooms(update=True)
                groups = self.channel.search_chatrooms(name=name)
            if not groups:
                return None
            for group in groups:
                if group.get("NickName") == name:
                    return group.get("UserName")
            return None
        elif self.channel_type == "ntchat":
            pass
        raise ValueError(f"不支持的channel_type: {self.channel_type}")