# TaskScheduler

## 简介

`TaskScheduler` 是一个用于定时执行任务或调用其他插件的插件，适用于 [chatgpt-on-wechat](https://github.com/zhayujie/chatgpt-on-wechat)。它基于 `APScheduler` 库，提供了灵活的任务管理和调度功能。

## 功能特性

- **定时任务**：支持基于 Cron 表达式、具体日期、周期性等多种方式触发任务。
- **任务管理**：可以添加、取消和列出任务。
- **插件调用**：支持在定时任务中调用其他插件的功能。
- **群组支持**：可以在指定群聊中触发任务

## 安装与配置

### 安装

1. 将 `TaskScheduler` 插件文件夹放置在插件目录中。
2. 确保已安装所有的依赖：
   ```bash
   pip install -r plugins/TaskScheduler/requirements.txt
   ```

### 配置

1. 在插件目录中创建 `config.json` 文件，配置示例如下：
   ```json
   {
     "max_workers": 30,
     "command_prefix": "task",
     "allow_call_other_plugins": true,
     "custom_commands": [
       {
         "key_word": "早报",
         "command_prefix": "$tool "
       },
       {
         "key_word": "点歌",
         "command_prefix": "$"
       },
       {
         "key_word": "任务列表",
         "command_prefix": "$task "
       }
     ]
   }
   ```
2. 配置说明：
   - `max_workers`：线程池的最大工作线程数。
   - `command_prefix`：触发任务的命令前缀。
   - `allow_call_other_plugins`：是否允许在任务中调用其他插件。
   - `custom_commands`：自定义命令配置，用于在任务中调用特定插件。

## 使用方法

命令与 [haikerapples/timetask](https://github.com/haikerapples/timetask) 基本相同

### 添加任务

#### 命令格式

- **Cron 表达式**：`$task cron[* * * * *] event_str`
- **周期性任务**：`$task cycle time_str event_str`

可选参数：
- `group[group_name]`：指定群聊

#### 示例

- 每天 8:30 执行任务：
  ```
  $task 每天 08:30 提醒我开会
  ```
- 使用 Cron 表达式：
  ```
  $task cron[30 8 * * *] 提醒我开会
  ```
- 指定群聊：
  ```
  $task 每天 08:30 提醒下班 group[工作群]
  ```
- 调用其他插件：
  ```
  $task 每天 08:30 $tool 早报
  ```

### 取消任务

#### 命令格式

```
$task 取消任务 task_id
```

#### 示例

```
$task 取消任务 1234567
```

### 列出任务

#### 命令格式

```
$task 任务列表
```


## 注意事项

必须合并 https://github.com/zhayujie/chatgpt-on-wechat/pull/2413 以及 https://github.com/zhayujie/chatgpt-on-wechat/pull/2407 才能保证正常工作，否则 `reloadp` 和 `scanp` 会造成任务重复执行。
