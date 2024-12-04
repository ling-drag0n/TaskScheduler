# encoding:utf-8
import hashlib
import time
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from channel.chat_channel import ChatChannel
from channel.chat_message import ChatMessage
from channel.channel_factory import create_channel
import datetime
import re
import uuid

from bridge.reply import Reply, ReplyType
import plugins
from common.log import logger
from bridge.context import ContextType
from plugins import *


from .tools import WrappedChannelTools

current_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(current_dir, "tasks.db")

class CheckedThreadPoolExecutor(ThreadPoolExecutor):
    def _do_submit_job(self, job, run_times):
        if not self._check_conditions(job):
            print(f"Job {job.id} conditions not met, skipping")
            return
        
        return super()._do_submit_job(job, run_times)
    
    def _check_conditions(self, job):
        msg:ChatMessage = job.args[3]
        channel = create_channel(conf().get("channel_type"))
        channel_tools = WrappedChannelTools()
        if isinstance(channel, ChatChannel):
            bot_user_id = channel.user_id
            if bot_user_id is None:
                return False
            logger.info("检查 id 是否变化")
            if msg.to_user_id != bot_user_id:
                # id 发生变化，尝试根据昵称更新（当然这是不可靠的, 因为昵称可以相同）
                logger.info("id 改变，尝试更新")
                try:
                    if msg.is_group:
                        group_id = channel_tools.get_group_id_by_name(msg.other_user_nickname)
                        if group_id is None:
                            raise ValueError(f"没有找到 {msg.other_user_nickname}")
                        msg.from_user_id = group_id
                        msg.other_user_id = group_id
                        msg.to_user_id = bot_user_id
                        # actual_user_id 就不更新了，反正也没用到
                    else:
                        friend_id = channel_tools.get_user_id_by_name(msg.other_user_nickname)
                        if friend_id is None:
                            raise ValueError(f"没有找到 {msg.other_user_nickname}")
                        msg.from_user_id = friend_id
                        msg.other_user_id = friend_id
                        msg.to_user_id = bot_user_id
                    job.args = (job.args[0], job.args[1], job.args[2], msg, job.args[4])
                    self._scheduler.modify_job(job.id, args=(job.args[0], job.args[1], job.args[2], msg, job.args[4]))
                except Exception as e:
                    logger.error(f"更新任务 {job.id} 失败: {e}")
            else:
                logger.info("id 无变化，无需更新")
        return True

@plugins.register(
    name="TaskScheduler",
    desire_priority=-1,
    namecn="计划任务",
    desc="定时执行任务或者调用其他插件",
    version="1.0",
    author="rikka",
)
class TaskScheduler(Plugin):
    def __del__(self):
        logger.info("[TaskScheduler] 关闭 scheduler")
        self.scheduler.shutdown()

    def __init__(self):
        super().__init__()
        try:
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()

            # self.handlers[Event.ON_HANDLE_CONTEXT] = weakref.WeakMethod(self.on_handle_context)
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

            jobstores = {
                "default": SQLAlchemyJobStore(
                    url=f"sqlite:///{db_path}"
                )  # 使用 SQLite 存储任务,位于当前文件同级目录
            }
            executors = {
                "default": CheckedThreadPoolExecutor(
                    max_workers=self.config.get("max_workers", 30)
                )
            }
            self.scheduler = BackgroundScheduler(
                jobstores=jobstores, executors=executors
            )

            # self.scheduler.add_listener(self.check_and_update_job, EVENT_JOB_SUBMITTED)
            self.scheduler.start()
            self.channel_tools = WrappedChannelTools()
            logger.info("[TaskScheduler] inited")
        except Exception as e:
            logger.error(f"[TaskScheduler] 初始化异常：{e}")
            raise ValueError("[TaskScheduler] init failed, ignore ")

    def generate_short_id(self):
        seed = f"{time.time()}{uuid.uuid4()}"
        sha1 = hashlib.sha1(seed.encode("utf-8")).hexdigest()
        return sha1[:7]

    def on_handle_context(self, e_context: EventContext):
        trigger_prefix = conf().get("plugin_trigger_prefix", "$")
        command_prefix = self.config.get("command_prefix", "time")
        channel_tools = WrappedChannelTools()

        if e_context["context"].type == ContextType.TEXT:
            content = e_context["context"].content
            if content.startswith(f"{trigger_prefix}{command_prefix}"):
                # 先拆分出 $time 和剩余部分
                _, remaining = content.split(f"{trigger_prefix}{command_prefix} ", 1)
                remaining = remaining.strip()

                operation = remaining.split(" ", 1)[0]
                if operation == "任务列表":
                    self.get_task_list(e_context)
                elif operation == "取消任务":
                    task_id = remaining.split(" ", 1)[1].strip()
                    self.cancel_task(e_context, task_id)
                else:
                    # 检查是否含有组名称, 必须以 group[组名称] 的形式出现, 且位于尾部
                    match = re.search(r"group\[(.*?)\]$", remaining)
                    if match:
                        group_name = match.group(1)
                    else:
                        group_name = None
                    # 解析剩余的部分
                    remaining = remaining.replace(f"group[{group_name}]", "").strip()

                    # 如果是 cron, 格式就是 cron[* * * * *] event_str
                    if remaining.startswith("cron["):
                        cron_end = remaining.find("]")
                        if cron_end != -1:
                            cron_part = remaining[: cron_end + 1]
                            event_part = remaining[cron_end + 1 :].strip()
                            parts = [cron_part, event_part]
                        else:
                            reply = Reply()
                            reply.type = ReplyType.TEXT
                            reply.content = "指令格式错误，cron 表达式不完整"
                            e_context["reply"] = reply
                            e_context.action = EventAction.BREAK_PASS
                            logger.error("指令格式错误，cron 表达式不完整")
                            return
                        self.add_task(
                            e_context,
                            cycle=parts[0],
                            event=parts[1],
                            group_name=group_name,
                        )
                    else:
                        # 否则就是普通周期和时间, 格式就是 cycle time_str event_str
                        parts = remaining.split(" ", 2)
                        if len(parts) != 3:
                            reply = Reply()
                            reply.type = ReplyType.TEXT
                            reply.content = "指令格式错误"
                            e_context["reply"] = reply
                            e_context.action = EventAction.BREAK_PASS
                            logger.error("指令格式错误")
                            return

                        self.add_task(
                            e_context,
                            cycle=parts[0],
                            time_str=parts[1],
                            event=parts[2],
                            group_name=group_name,
                        )

    def _load_config_template(self):
        logger.debug(
            "No TaskScheduler plugin config.json, use plugins/taskscheduler/config.json.template"
        )
        try:
            plugin_config_path = os.path.join(self.path, "config.json.template")
            if os.path.exists(plugin_config_path):
                with open(plugin_config_path, "r", encoding="utf-8") as f:
                    plugin_conf = json.load(f)
                    return plugin_conf
        except Exception as e:
            logger.exception(e)

    # 解析事件与组
    def parse_event_and_group(self, event_str):
        match = re.search(r"group\[(.*?)\]", event_str)
        if match:
            group_name = match.group(1)
            event = re.sub(r"group\[.*?\]", "", event_str).strip()
        else:
            group_name = None
            event = event_str
        return event, group_name

    # 根据周期和时间生成 Trigger
    def get_trigger(self, cycle: str, time_str: str | None = None):
        """
        根据周期和时间生成对应的 Trigger
        :param cycle: 表示周期的字符串 (例如: "每天", "2024-12-03", "cron[30 6 * * *]")
        :param time_str: 表示时间的字符串 (例如: "08:30")
        """
        try:
            # 解析 Cron 表达式，例如 "cron[0 8 * * *]"
            if cycle.startswith("cron[") and cycle.endswith("]"):
                cron_expr = cycle[5:-1]
                return CronTrigger.from_crontab(cron_expr)

            # 解析具体日期周期，例如 "2024-12-03"
            if re.match(r"\d{4}-\d{2}-\d{2}", cycle):
                if not time_str:
                    raise ValueError("必须提供时间字符串 time_str")
                date_obj = datetime.datetime.strptime(cycle, "%Y-%m-%d").date()
                time_obj = datetime.datetime.strptime(time_str, "%H:%M").time()
                run_date = datetime.datetime.combine(date_obj, time_obj)
                return DateTrigger(run_date=run_date)

            # 解析今天、明天、后天
            if cycle in ["今天", "明天", "后天"]:
                if not time_str:
                    raise ValueError("必须提供时间字符串 time_str")
                offset_days = {"今天": 0, "明天": 1, "后天": 2}[cycle]
                base_date = datetime.datetime.today().date() + datetime.timedelta(
                    days=offset_days
                )
                time_obj = datetime.datetime.strptime(time_str, "%H:%M").time()
                run_date = datetime.datetime.combine(base_date, time_obj)
                return DateTrigger(run_date=run_date)

            # 解析每周指定日，例如 "每周一"
            if cycle.startswith("每周"):
                if not time_str:
                    raise ValueError("必须提供时间字符串 time_str")
                weekday_name = cycle[2:]
                weekday_mapping = {
                    "一": 0,
                    "二": 1,
                    "三": 2,
                    "四": 3,
                    "五": 4,
                    "六": 5,
                    "日": 6,
                }
                if weekday_name not in weekday_mapping:
                    raise ValueError(f"无效的星期: {weekday_name}")
                weekday = weekday_mapping[weekday_name]
                hour, minute = map(int, time_str.split(":"))
                return CronTrigger(day_of_week=weekday, hour=hour, minute=minute)

            # 默认处理每日、工作日等周期
            if cycle == "每天":
                if not time_str:
                    raise ValueError("必须提供时间字符串 time_str")
                hour, minute = map(int, time_str.split(":"))
                return CronTrigger(hour=hour, minute=minute)
            if cycle == "工作日":
                if not time_str:
                    raise ValueError("必须提供时间字符串 time_str")
                hour, minute = map(int, time_str.split(":"))
                return CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute)

            # 无法匹配的周期规则
            raise ValueError(f"无法解析周期: {cycle}")
        except Exception as e:
            raise ValueError(f"生成 Trigger 时出错: {e}")

    # 添加任务
    def add_task(
        self,
        e_context: EventContext,
        event: str,
        cycle: str,
        time_str: str | None = None,
        group_name: str | None = None,
    ):
        reply = Reply()
        reply.type = ReplyType.TEXT
        try:
            task_id = self.generate_short_id()
            # event, group_name = parse_event_and_group(event)
            trigger = self.get_trigger(cycle, time_str)
            _msg = e_context["context"]["msg"]
            msg = ChatMessage({})
            msg.ctype = ContextType.TEXT
            msg.content = event

            # 在群聊中触发
            if _msg.is_group:
                # 提供 group_name
                if group_name is not None:
                    group_id = self.channel_tools.get_group_id_by_name(group_name)
                    if group_id is None:
                        raise ValueError(f"没有找到 {group_name}")
                    msg.actual_user_id = _msg.actual_user_id
                    msg.actual_user_nickname = _msg.actual_user_nickname
                    msg.from_user_id = group_id
                    msg.from_user_nickname = group_name
                    msg.other_user_id = group_id
                    msg.other_user_nickname = group_name
                    msg.is_group = True
                    msg.to_user_id = _msg.to_user_id
                    msg.to_user_nickname = _msg.to_user_nickname
                    msg.is_at = True
                    no_need_at = True
                # 没有提供 group_name
                else:
                    msg.actual_user_id = _msg.actual_user_id
                    msg.actual_user_nickname = _msg.actual_user_nickname
                    msg.from_user_id = _msg.from_user_id
                    msg.from_user_nickname = _msg.from_user_nickname
                    msg.other_user_id = _msg.other_user_id
                    msg.other_user_nickname = _msg.other_user_nickname
                    msg.to_user_id = _msg.to_user_id
                    msg.to_user_nickname = _msg.to_user_nickname
                    msg.is_group = True
                    msg.is_at = True
                    no_need_at = False
            # 私聊中触发
            else:
                # 提供 group_name
                if group_name is not None:
                    group_id = self.channel_tools.get_group_id_by_name(group_name)
                    if group_id is None:
                        raise ValueError(f"没有找到 {group_name}")

                    msg.actual_user_id = _msg.from_user_id
                    msg.actual_user_nickname = _msg.from_user_nickname
                    msg.from_user_id = group_id
                    msg.from_user_nickname = group_name
                    msg.other_user_id = group_id
                    msg.other_user_nickname = group_name
                    msg.to_user_id = _msg.to_user_id
                    msg.to_user_nickname = _msg.to_user_nickname
                    msg.is_at = True
                    msg.is_group = True
                    no_need_at = True
                # 没有提供 group_name
                else:
                    msg.from_user_id = _msg.from_user_id
                    msg.from_user_nickname = _msg.from_user_nickname
                    msg.other_user_id = _msg.other_user_id
                    msg.other_user_nickname = _msg.other_user_nickname
                    msg.to_user_id = _msg.to_user_id
                    msg.to_user_nickname = _msg.to_user_nickname
                    msg.is_group = False
                    no_need_at = False

            self.scheduler.add_job(
                task_execute,
                trigger,
                id=task_id,
                args=(
                    task_id,
                    event,
                    group_name,
                    msg,
                    no_need_at
                ),
                misfire_grace_time=60
            )
            logger.info(f"任务添加成功，任务编号: {task_id}")
            reply.content = f"任务添加成功，任务编号: {task_id}"
        except Exception as e:
            logger.error(f"添加任务失败: {e}")
            reply.content = f"添加任务失败: {e}"
        e_context["reply"] = reply
        e_context.action = EventAction.BREAK_PASS

    # 取消任务
    def cancel_task(self, e_context: EventContext, task_id: str):
        reply = Reply()
        reply.type = ReplyType.TEXT
        try:
            self.scheduler.remove_job(task_id)
            logger.info(f"任务 {task_id} 已取消")
            reply.content = f"任务 {task_id} 已取消"
        except JobLookupError:
            logger.error(f"任务 {task_id} 不存在")
            reply.content = f"任务 {task_id} 不存在"
        e_context["reply"] = reply
        e_context.action = EventAction.BREAK_PASS

    # 列出任务
    def get_task_list(self, e_context: EventContext):
        reply = Reply()
        reply.type = ReplyType.TEXT
        results = []
        jobs = self.scheduler.get_jobs()
        if jobs:
            logger.info("任务列表:")
            results.append("任务列表:")
            for i, job in enumerate(jobs):
                logger.info(f"任务编号: {job.id}")
                results.append(f"任务编号: {job.id}")
                logger.info(f"下次运行时间: {job.next_run_time}")
                results.append(f"下次运行时间: {job.next_run_time}")
                logger.info(f"任务内容: {job.args[1]}")
                results.append(f"任务内容: {job.args[1]}")
                if job.args[2]:
                    logger.info(f"群组: {job.args[2]}")
                    results.append(f"群组: {job.args[2]}")
                if i != len(jobs) - 1:
                    logger.info("--------------------")
                    results.append("--------------------")
        else:
            logger.info("没有任务")
            results.append("没有任务")
        reply.content = "\n".join(results)
        e_context["reply"] = reply
        e_context.action = EventAction.BREAK_PASS
    
                 
# 执行任务
def task_execute(
    task_id: str,
    event: str,
    group_name: str | None,
    msg:ChatMessage,
    no_need_at
):
    logger.info(f"开始执行任务{task_id}: {event}, 组: {group_name}")
    prefix = conf().get("single_chat_prefix", [""])


    if pconf('TaskScheduler').get('allow_call_other_plugins', True):
        call_other_plugins = True
        for custom_command in pconf('TaskScheduler').get('custom_commands', []):
            if event.startswith(custom_command['key_word']):
                event = f"{custom_command['command_prefix']}{event}"
                break

    content = f"{prefix[0]} {event}" if not msg.is_group else event
    content_dict = {
        "no_need_at": no_need_at,
        "isgroup": msg.is_group,
        "msg": msg,
    }

    # channel 是单例的
    channel = create_channel(conf().get("channel_type"))
    if isinstance(channel, ChatChannel):
        context = channel._compose_context(
            ContextType.TEXT, content, **content_dict
        )
        try:
            if call_other_plugins:
                e_context = PluginManager().emit_event(
                    EventContext(
                        Event.ON_HANDLE_CONTEXT,
                        {"channel": channel, "context": context, "reply": Reply()},
                    )
                )
            if e_context["reply"]:
                reply = e_context["reply"]
                if not reply.content:
                    reply.type = ReplyType.TEXT
                    reply.content = f"【执行定时任务】\n任务编号: {task_id}\n任务内容: {event}"
                if msg.is_group and reply.type == ReplyType.TEXT and not context.get('no_need_at', False):
                    reply.content = f"@{msg.actual_user_nickname}\n{reply.content}"
                channel.send(reply, context)

        except Exception as e:
            logger.error(f"执行任务失败: {e}")
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = f"执行任务失败: {e}"
            if context:
                channel.send(reply, context)

