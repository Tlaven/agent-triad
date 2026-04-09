import tkinter as tk
from tkinter import messagebox

class TodoApp:
    def __init__(self, root):
        """
        初始化待办事项应用
        Args:
            root: tkinter主窗口
        """
        self.root = root
        self.root.title("待办事项应用")
        self.root.geometry("500x400")
        
        # 任务列表，每个任务是一个元组 (任务内容, 是否完成)
        self.tasks = []
        
        # 创建界面组件
        self.create_widgets()
    
    def create_widgets(self):
        """创建所有界面组件"""
        # 标题
        title_label = tk.Label(self.root, text="我的待办事项", font=("Arial", 16, "bold"))
        title_label.pack(pady=10)
        
        # 任务显示区域 - 使用Listbox
        self.task_listbox = tk.Listbox(self.root, width=60, height=15, font=("Arial", 11))
        self.task_listbox.pack(pady=10, padx=20)
        
        # 滚动条
        scrollbar = tk.Scrollbar(self.task_listbox)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.task_listbox.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.task_listbox.yview)
        
        # 输入区域
        input_frame = tk.Frame(self.root)
        input_frame.pack(pady=10)
        
        self.task_entry = tk.Entry(input_frame, width=40, font=("Arial", 11))
        self.task_entry.pack(side=tk.LEFT, padx=5)
        self.task_entry.bind('<Return>', lambda e: self.add_task())  # 回车键添加任务
        
        add_button = tk.Button(input_frame, text="添加任务", command=self.add_task, 
                              bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
        add_button.pack(side=tk.LEFT, padx=5)
        
        # 按钮区域
        button_frame = tk.Frame(self.root)
        button_frame.pack(pady=10)
        
        complete_button = tk.Button(button_frame, text="标记完成", command=self.complete_task,
                                   bg="#2196F3", fg="white", font=("Arial", 10, "bold"), width=12)
        complete_button.pack(side=tk.LEFT, padx=5)
        
        delete_button = tk.Button(button_frame, text="删除任务", command=self.delete_task,
                                 bg="#f44336", fg="white", font=("Arial", 10, "bold"), width=12)
        delete_button.pack(side=tk.LEFT, padx=5)
        
        # 状态标签
        self.status_label = tk.Label(self.root, text="共 0 个任务", fg="gray")
        self.status_label.pack(pady=5)
    
    def add_task(self):
        """
        添加新任务
        从输入框获取任务文本，验证后添加到任务列表
        """
        task_text = self.task_entry.get().strip()
        if not task_text:
            messagebox.showwarning("提示", "请输入任务内容")
            return
        
        self.tasks.append((task_text, False))  # False表示未完成
        self.task_entry.delete(0, tk.END)
        self.refresh_task_list()
    
    def delete_task(self):
        """
        删除选中的任务
        从任务列表中移除选中的任务
        """
        selection = self.task_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的任务")
            return
        
        index = selection[0]
        del self.tasks[index]
        self.refresh_task_list()
    
    def complete_task(self):
        """
        标记/取消标记任务完成状态
        切换选中任务的完成状态
        """
        selection = self.task_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要标记的任务")
            return
        
        index = selection[0]
        task_text, completed = self.tasks[index]
        self.tasks[index] = (task_text, not completed)  # 切换完成状态
        self.refresh_task_list()
    
    def refresh_task_list(self):
        """刷新任务列表显示"""
        self.task_listbox.delete(0, tk.END)
        for task_text, completed in self.tasks:
            display_text = f"✓ {task_text}" if completed else f"  {task_text}"
            self.task_listbox.insert(tk.END, display_text)
            # 为已完成的任务设置不同颜色
            if completed:
                self.task_listbox.itemconfig(tk.END, {'fg': 'gray'})
        
        # 更新状态标签
        total = len(self.tasks)
        completed_count = sum(1 for _, c in self.tasks if c)
        self.status_label.config(text=f"共 {total} 个任务，已完成 {completed_count} 个")

def main():
    """应用入口函数"""
    root = tk.Tk()
    app = TodoApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
