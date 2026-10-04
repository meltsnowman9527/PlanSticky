"""计划便签 (PlanSticky) —— 一个常驻桌面的轻量计划管理便签。

v1.1 起并入原「清楚账本」的记账 / 日记 / 每日签到，全部用 PySide6 原生控件重写
（不内嵌浏览器），并与旧版数据库格式保持一致。

模块划分：
    计划部分
        config          路径与常量
        database        计划数据层（tasks + 设置）
        task_row        单条计划行（勾选/行内编辑/菜单/拖拽）
        task_list       计划滚动列表（完成分区 + 拖拽排序）
        add_bar         底部“添加计划”输入条
        day_list        短期页「多日列表」视图
        heatmap         短期页「完成图」月历
        calendar_popup  日期选择弹层
    账本部分
        ledger_db       账本数据层（账目/日记/签到/标签/预算）
        migrate_money   旧「清楚账本」数据一次性导入（只拷贝）
        ledger_page     记账页（表单/概览/明细/筛选/CSV/备份/媒体清理）
        ledger_charts   自绘趋势柱状图 + 分类环形图
        journal_html    日记 HTML 归一化（Qt 文档 ↔ 旧版 div/br/img/video）
        journal_page    日记页（富文本/图片/视频/归档）
        checkin_page    签到页（自绘月历/标签/心情）
    公共部分
        theme           浅色/深色/跟随系统
        icons           程序/托盘图标（代码绘制）
        ui_common       通用控件（复选框/开关/把手/省略标签/分段 Tab/提示条/流式布局）
        singleinstance  单实例保护
        main_window     主悬浮窗口（无边框、缩放、几何记忆、5 个 Tab）
        tray            系统托盘
        app             组装入口 + 全局异常兜底 + 首启动迁移
"""

__version__ = "1.1.0"
