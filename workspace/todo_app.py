#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
To-Do List 待办事项应用
使用 Python 内置的 tkinter 库实现，无需额外安装第三方库
功能：添加待办、查看列表、标记完成、删除事项
支持 GUI 和 CLI 两种模式
"""

import tkinter as tk
from tkinter import messagebox
import json
import os
import sys
import argparse


# 数据文件路径
DATA_FILE = "todo_data.json"

# Windows 控制台编码处理
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass


class TodoApp:
    """待办事项应用主类（GUI模式）"""
    
    def __init__(self, root):
        """初始化应用界面"""
        self.root = root
        self.root.title("To-Do List 待办事项")
        self.root.geometry("500x450")
        
        # 存储待办事项的列表，每个元素为 (任务文本, 完成状态) 元组
        self.tasks = []
        
        # 初始化界面组件
        self._create_widgets()
        
    def _create_widgets(self):
        """创建界面组件"""
        # 标题标签
        title_label = tk.Label(self.root, text="我的待办事项", 
                               font=("Arial", 18, "bold"), fg="#333")
        title_label.pack(pady=10)
        
        # 输入框和添加按钮的框架
        input_frame = tk.Frame(self.root)
        input_frame.pack(pady=10, padx=10, fill=tk.X)
        
        # 任务输入框
        self.task_entry = tk.Entry(input_frame, font=("Arial", 12), width=30)
        self.task_entry.pack(side=tk.LEFT, padx=(0, 10), fill=tk.X, expand=True)
        
        # 绑定回车键事件
        self.task_entry.bind("<Return>", lambda e: self.add_task())
        
        # 添加按钮
        add_btn = tk.Button(input_frame, text="添加", command=self.add_task,
                           bg="#4CAF50", fg="white", font=("Arial", 11),
                           width=8, relief=tk.FLAT)
        add_btn.pack(side=tk.LEFT)
        
        # 待办事项列表框架（带滚动条）
        list_frame = tk.Frame(self.root, bg="white", bd=1, relief=tk.SUNKEN)
        list_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        
        # 滚动条
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 任务列表框
        self.task_listbox = tk.Listbox(list_frame, font=("Arial", 11),
                                       yscrollcommand=scrollbar.set,
                                       selectmode=tk.SINGLE,
                                       bg="#f9f9f9", bd=0,
                                       highlightthickness=0)
        self.task_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.task_listbox.yview)
        
        # 按钮操作框架
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=10)
        
        # 标记完成按钮
        complete_btn = tk.Button(btn_frame, text="标记完成", 
                                command=self.mark_complete,
                                bg="#2196F3", fg="white", font=("Arial", 10),
                                width=12, relief=tk.FLAT, pady=5)
        complete_btn.grid(row=0, column=0, padx=5)
        
        # 删除按钮
        delete_btn = tk.Button(btn_frame, text="删除", 
                              command=self.delete_task,
                              bg="#f44336", fg="white", font=("Arial", 10),
                              width=12, relief=tk.FLAT, pady=5)
        delete_btn.grid(row=0, column=1, padx=5)
        
        # 状态标签
        self.status_label = tk.Label(self.root, text="共 0 项待办", 
                                     font=("Arial", 9), fg="#888")
        self.status_label.pack(pady=5)
        
    def add_task(self):
        """添加新任务"""
        task_text = self.task_entry.get().strip()
        
        if not task_text:
            messagebox.showwarning("提示", "请输入待办事项内容！")
            return
        
        # 添加到任务列表 (False 表示未完成)
        self.tasks.append((task_text, False))
        self.task_entry.delete(0, tk.END)
        self.update_list()
        
    def mark_complete(self):
        """标记选中的任务为完成状态"""
        selected = self.task_listbox.curselection()
        
        if not selected:
            messagebox.showwarning("提示", "请先选择要完成的事项！")
            return
        
        index = selected[0]
        task_text, _ = self.tasks[index]
        self.tasks[index] = (task_text, True)
        self.update_list()
        
    def delete_task(self):
        """删除选中的任务"""
        selected = self.task_listbox.curselection()
        
        if not selected:
            messagebox.showwarning("提示", "请先选择要删除的事项！")
            return
        
        index = selected[0]
        
        # 确认删除
        if messagebox.askyesno("确认", "确定要删除这项待办吗？"):
            del self.tasks[index]
            self.update_list()
            
    def update_list(self):
        """更新列表显示"""
        self.task_listbox.delete(0, tk.END)
        
        for i, (task_text, is_complete) in enumerate(self.tasks):
            # 根据完成状态显示不同样式
            if is_complete:
                display_text = f"[完成] {task_text}"
                self.task_listbox.insert(tk.END, display_text)
                # 设置已完成项目为灰色
                self.task_listbox.itemconfig(i, fg="#888")
            else:
                display_text = f"[待办] {task_text}"
                self.task_listbox.insert(tk.END, display_text)
                self.task_listbox.itemconfig(i, fg="#000")
        
        # 更新状态栏
        total = len(self.tasks)
        completed = sum(1 for _, is_complete in self.tasks if is_complete)
        self.status_label.config(text=f"共 {total} 项，已完成 {completed} 项")


class TodoCLI:
    """命令行模式的待办事项应用"""
    
    def __init__(self, data_file=None):
        """初始化 CLI 应用"""
        self.data_file = data_file or DATA_FILE
        self.tasks = []
        self.load_tasks()
    
    def load_tasks(self):
        """从文件加载任务"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 兼容旧格式和新格式
                    if isinstance(data, list):
                        self.tasks = data
                    elif isinstance(data, dict) and 'tasks' in data:
                        self.tasks = data['tasks']
            except (json.JSONDecodeError, IOError):
                self.tasks = []
    
    def save_tasks(self):
        """保存任务到文件"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.tasks, f, ensure_ascii=False, indent=2)
            return True
        except IOError as e:
            print(f"保存失败: {e}")
            return False
    
    def add_task(self, task_text):
        """添加新任务"""
        if not task_text:
            print("错误: 任务内容不能为空")
            return False
        
        self.tasks.append([task_text, False])
        if self.save_tasks():
            print(f"[OK] 已添加: {task_text}")
            return True
        return False
    
    def list_tasks(self):
        """列出所有任务"""
        if not self.tasks:
            print("\n当前没有待办事项")
            return
        
        print("\n" + "=" * 50)
        print("待办事项列表:")
        print("=" * 50)
        
        pending_count = 0
        completed_count = 0
        
        for i, (task_text, is_complete) in enumerate(self.tasks, 1):
            if is_complete:
                print(f"  [{i}] [完成] {task_text}")
                completed_count += 1
            else:
                print(f"  [{i}] [待办] {task_text}")
                pending_count += 1
        
        print("-" * 50)
        print(f"总计: {len(self.tasks)} 项 | 待办: {pending_count} | 已完成: {completed_count}")
        print("=" * 50)
    
    def complete_task(self, index):
        """标记任务为完成"""
        if index < 1 or index > len(self.tasks):
            print(f"错误: 无效的任务编号 {index}")
            return False
        
        task_text, is_complete = self.tasks[index - 1]
        if is_complete:
            print(f"任务 [{index}] 已经完成")
            return False
        
        self.tasks[index - 1] = [task_text, True]
        if self.save_tasks():
            print(f"[OK] 已标记完成: {task_text}")
            return True
        return False
    
    def delete_task(self, index):
        """删除任务"""
        if index < 1 or index > len(self.tasks):
            print(f"错误: 无效的任务编号 {index}")
            return False
        
        task_text, _ = self.tasks[index - 1]
        del self.tasks[index - 1]
        if self.save_tasks():
            print(f"[OK] 已删除: {task_text}")
            return True
        return False
    
    def run_interactive(self):
        """交互式运行 CLI"""
        print("\n欢迎使用 To-Do List 命令行版本!")
        print("输入 help 查看帮助命令\n")
        
        while True:
            try:
                command = input("todo> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n退出程序")
                break
            
            if not command:
                continue
            
            parts = command.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            
            if cmd in ('quit', 'exit', 'q'):
                print("再见!")
                break
            elif cmd in ('help', 'h', '?'):
                self.show_help()
            elif cmd in ('list', 'ls', 'l'):
                self.list_tasks()
            elif cmd in ('add', 'a'):
                if arg:
                    self.add_task(arg)
                else:
                    print("用法: add <任务内容>")
            elif cmd in ('complete', 'done', 'c'):
                if arg:
                    try:
                        self.complete_task(int(arg))
                    except ValueError:
                        print("用法: complete <任务编号>")
                else:
                    print("用法: complete <任务编号>")
            elif cmd in ('delete', 'del', 'd', 'rm'):
                if arg:
                    try:
                        self.delete_task(int(arg))
                    except ValueError:
                        print("用法: delete <任务编号>")
                else:
                    print("用法: delete <任务编号>")
            elif cmd in ('clear', 'cls'):
                # 简单清屏
                print("\n" * 50)
            else:
                print(f"未知命令: {cmd}")
                print("输入 help 查看可用命令")
    
    def show_help(self):
        """显示帮助信息"""
        print("""
可用命令:
  add <内容>      - 添加新待办事项
  list            - 查看所有待办事项
  complete <编号> - 标记任务为完成
  delete <编号>   - 删除任务
  clear           - 清屏
  help            - 显示帮助
  quit/exit       - 退出程序

示例:
  todo> add 买牛奶
  todo> list
  todo> complete 1
  todo> delete 2
""")


def main():
    """主函数：启动应用"""
    parser = argparse.ArgumentParser(
        description='To-Do List 待办事项应用',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python todo_app.py              # 启动 GUI 模式
  python todo_app.py --cli        # 启动 CLI 交互模式
  python todo_app.py --cli add "买牛奶"
  python todo_app.py --cli list
  python todo_app.py --cli complete 1
  python todo_app.py --cli delete 2
        """
    )
    parser.add_argument(
        '--cli', 
        action='store_true',
        help='使用命令行模式（CLI）'
    )
    parser.add_argument(
        '--file', 
        default=DATA_FILE,
        help=f'数据文件路径 (默认: {DATA_FILE})'
    )
    parser.add_argument(
        'command',
        nargs='?',
        help='CLI 命令: add, list, complete, delete, help'
    )
    parser.add_argument(
        'args',
        nargs='*',
        help='命令参数'
    )
    
    args = parser.parse_args()
    
    if args.cli or args.command:
        # CLI 模式
        cli = TodoCLI(data_file=args.file)
        
        if args.command:
            # 单命令模式
            cmd = args.command.lower()
            if cmd == 'add':
                if args.args:
                    task_text = ' '.join(args.args)
                    cli.add_task(task_text)
                else:
                    print("用法: todo_app.py --cli add <任务内容>")
            elif cmd in ('list', 'ls'):
                cli.list_tasks()
            elif cmd in ('complete', 'done', 'c'):
                if args.args:
                    try:
                        cli.complete_task(int(args.args[0]))
                    except ValueError:
                        print("用法: todo_app.py --cli complete <任务编号>")
                else:
                    print("用法: todo_app.py --cli complete <任务编号>")
            elif cmd in ('delete', 'del', 'd', 'rm'):
                if args.args:
                    try:
                        cli.delete_task(int(args.args[0]))
                    except ValueError:
                        print("用法: todo_app.py --cli delete <任务编号>")
                else:
                    print("用法: todo_app.py --cli delete <任务编号>")
            elif cmd in ('help', 'h', '?'):
                cli.show_help()
            else:
                print(f"未知命令: {cmd}")
                print("用法: todo_app.py --cli help")
        else:
            # 交互模式
            cli.run_interactive()
    else:
        # GUI 模式
        root = tk.Tk()
        app = TodoApp(root)
        root.mainloop()


if __name__ == "__main__":
    main()