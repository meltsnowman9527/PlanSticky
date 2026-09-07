"""计划便签 (PlanSticky) —— 一个常驻桌面的轻量计划管理便签。

模块划分：
    config          路径与常量
    database        SQLite 数据层（自动保存）
    theme           浅色/深色/跟随系统 主题
    icons           程序/托盘图标（代码绘制，无需资源文件）
    singleinstance  单实例保护
    ui_common       通用小控件（复选框、开关、拖拽把手、文字标签等）
    task_row        单条计划行（勾选/行内编辑/菜单/拖拽）
    task_list       计划滚动列表（完成分区 + 拖拽排序）
    add_bar         底部“添加计划”输入条
    calendar_popup  日期选择弹层
    tray            系统托盘
    main_window     主悬浮窗口（无边框、缩放、几何记忆、两个 Tab）
    app             组装入口
"""

__version__ = "1.0.0"
