"""Native Windows dialogs, shared CLI builder, optional explicit ADB selection."""
import json,os,queue,threading,traceback,tkinter as tk
from tkinter import ttk,filedialog,messagebox
from pathlib import Path
from sources import Inputs,list_devices
from workflow_origin import build
from media_patch import ROOT
from module_output import normalize_author,export_archive

class App:
    def __init__(self,root):
        self.root=root;root.title('欧加相机 · OriginOS OTA 助手 v0.1.2（另存为 / 自定义作者）');root.geometry('980x820')
        self.events=queue.Queue();self.busy=False;self.devices={}
        self.last_result=None;self.save_dir=Path.home()/'Videos'
        self.values={k:tk.StringVar() for k in ('target','stock','dump','output','toolchain','device','author')}
        self.adb=tk.BooleanVar();self.adb_target=tk.BooleanVar()
        style=ttk.Style();style.theme_use('vista' if 'vista' in style.theme_names() else 'clam')
        box=ttk.Frame(root,padding=20);box.pack(fill='both',expand=True)
        ttk.Label(box,text='欧加相机 · OriginOS 离线适配',font=('Microsoft YaHei UI',19,'bold')).pack(anchor='w')
        ttk.Label(box,text='选择目标系统与本机原厂解包。无需旧模块；不会自动刷入、重启或清除数据。').pack(anchor='w',pady=(6,14))
        for key,title,isfile in [('target','目标 OriginOS 解包目录',False),('stock','本机欧加官方解包目录',False),
                                 ('dump','可选：原厂 dumpsys / HAL 导出 TXT',True),('output','构建缓存及报告目录',False),
                                 ('toolchain','Windows NDK 的 bin 目录（开发版）',False)]:
            row=ttk.Frame(box);row.pack(fill='x',pady=4)
            ttk.Label(row,text=title,width=34).pack(side='left')
            ttk.Entry(row,textvariable=self.values[key]).pack(side='left',fill='x',expand=True)
            ttk.Button(row,text='选择…',command=lambda k=key,f=isfile:self.browse(k,f)).pack(side='left',padx=(8,0))
        row=ttk.Frame(box);row.pack(fill='x',pady=4)
        ttk.Label(row,text='模块作者（author）',width=34).pack(side='left')
        ttk.Entry(row,textvariable=self.values['author']).pack(side='left',fill='x',expand=True)
        ttk.Label(row,text='留空默认：科比').pack(side='left',padx=(8,0))
        ttk.Label(box,text='构建完成后自动弹出“另存为”，选择成品 ZIP 的位置和名称；取消也不会丢失成品。').pack(anchor='w',pady=(4,0))
        portable=ROOT/'toolchain/bin'
        if portable.is_dir():self.values['toolchain'].set(str(portable))
        self.values['output'].set(str(Path.home()/'Videos/欧加相机OriginOS输出'))
        frame=ttk.LabelFrame(box,text='可选 ADB 备用取材（默认关闭）',padding=10);frame.pack(fill='x',pady=12)
        ttk.Checkbutton(frame,text='允许只读设备取材：本地缺少的 vendor/odm 文件、镜头与 tag 导出',variable=self.adb).pack(anchor='w')
        ttk.Checkbutton(frame,text='也允许补充目标系统核心文件（请确认手机当前运行的就是目标 OriginOS）',variable=self.adb_target).pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=6)
        self.combo=ttk.Combobox(row,textvariable=self.values['device'],state='readonly');self.combo.pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='刷新设备',command=self.refresh).pack(side='left',padx=8)
        ttk.Label(frame,text='多台在线设备不默认选第一台。OriginOS 的 system/product 不能冒充未运行的原厂 ColorOS。').pack(anchor='w')
        row=ttk.Frame(box);row.pack(fill='x',pady=(0,8))
        self.button=ttk.Button(row,text='从零构建模块',command=self.start);self.button.pack(side='left')
        self.save_button=ttk.Button(row,text='另存成品 ZIP…',command=self.save_result,state='disabled')
        self.save_button.pack(side='left',padx=8)
        ttk.Button(row,text='查看手动制作教程',command=self.docs).pack(side='left',padx=8)
        self.status=tk.StringVar(value='就绪：已接入原生修补、独立杜比、缩略图与 AI 组件；跨机型须验收。')
        ttk.Label(box,textvariable=self.status,wraplength=900).pack(anchor='w',pady=5)
        self.logs=tk.Text(box,wrap='word',font=('Consolas',10),state='disabled');self.logs.pack(fill='both',expand=True)
        self.root.after(150,self.poll)
    def browse(self,key,isfile):
        if self.busy:return
        p=filedialog.askopenfilename(title='选择运行时相机导出',filetypes=[('文本文件','*.txt *.log'),('所有文件','*.*')]) if isfile else filedialog.askdirectory(title='选择'+key)
        if p:self.values[key].set(p)
    def docs(self):
        path=ROOT/'从零手动制作欧加相机_OriginOS.md'
        if os.name=='nt':os.startfile(path)
        else:messagebox.showinfo('教程',str(path))
    def refresh(self):
        if self.busy:return
        def worker():
            try:self.events.put(('devices',list_devices()))
            except Exception as e:self.events.put(('error','ADB读取失败：'+str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def start(self):
        if self.busy:return
        v={k:x.get().strip() for k,x in self.values.items()}
        if not v['output'] or not v['toolchain']:
            messagebox.showerror('缺少路径','请选择构建缓存目录和 Windows NDK bin 目录。');return
        try:author=normalize_author(v['author'])
        except ValueError as exc:messagebox.showerror('作者格式不正确',str(exc),parent=self.root);return
        if not v['target'] and not (self.adb.get() and self.adb_target.get()):
            messagebox.showerror('缺少目标原件','请选择目标 OriginOS 解包目录，或明确启用当前系统 ADB 备用取材。');return
        if self.adb.get() and len(self.devices)>1 and v['device'] not in self.devices:
            messagebox.showerror('选择设备','多台设备在线，请先选择取材对象。');return
        inputs=Inputs(v['target'],v['stock'],self.adb.get(),self.devices.get(v['device'],''),self.adb_target.get(),True,v['dump'])
        self.last_result=None;self.save_button.configure(state='disabled')
        self.busy=True;self.button.configure(state='disabled');self.status.set('正在构建；完成后选择 ZIP 保存位置，不会操作刷机或重启。')
        def worker():
            try:
                archive,report=build(inputs,Path(v['output']),Path(v['toolchain']),lambda text:self.events.put(('log',text)),author=author)
                self.events.put(('done',(str(archive),report)))
            except Exception as exc:self.events.put(('failed',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    def save_result(self):
        if self.busy or not self.last_result:return
        path,report=self.last_result;source=Path(path)
        destination=filedialog.asksaveasfilename(parent=self.root,title='保存欧加相机模块 ZIP',
            initialdir=str(self.save_dir),initialfile=source.name,defaultextension='.zip',
            filetypes=[('Magisk / KernelSU 模块 ZIP','*.zip')])
        if not destination:
            self.status.set('已取消另存，成品仍保留：'+str(source))
            return
        self.save_dir=Path(destination).parent
        self.busy=True;self.button.configure(state='disabled');self.save_button.configure(state='disabled')
        self.status.set('正在保存 ZIP，请稍候：'+destination)
        def worker():
            try:
                saved=export_archive(source,destination)
                self.events.put(('saved',(str(saved),str(source),report)))
            except Exception as exc:self.events.put(('save_failed',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='devices':
                    self.devices={f"{x['serial']}  {x.get('details','')}":x['serial'] for x in data if x['state']=='device'}
                    self.combo.configure(values=list(self.devices));self.values['device'].set(next(iter(self.devices)) if len(self.devices)==1 else '')
                elif kind=='log':
                    self.logs.configure(state='normal');self.logs.insert('end',data+'\n');self.logs.see('end');self.logs.configure(state='disabled')
                elif kind=='done':
                    self.busy=False;self.button.configure(state='normal');path,report=data
                    self.last_result=(path,report);self.save_button.configure(state='normal')
                    self.status.set('构建完成（未刷入、未实机验收）：'+path)
                    self.save_result()
                elif kind=='saved':
                    self.busy=False;self.button.configure(state='normal');self.save_button.configure(state='normal')
                    saved,source,report=data;self.status.set('成品已保存（未刷入）：'+saved)
                    messagebox.showinfo('模块已保存',saved+'\n\n作者：'+report.get('module_author','科比')+
                        '\n警告数：'+str(len(report['warnings']))+'\n构建报告与教程保留在：'+str(Path(source).parent)+
                        '\n未自动刷入，实机功能仍需验收。',parent=self.root)
                elif kind=='save_failed':
                    self.busy=False;self.button.configure(state='normal');self.save_button.configure(state='normal')
                    self.status.set('另存失败，原成品仍保留；可点击“另存成品 ZIP…”重试。')
                    messagebox.showerror('保存失败',data+'\n原成品没有删除，请换一个保存位置重试。',parent=self.root)
                elif kind in ('error','failed'):
                    if kind=='failed':self.busy=False;self.button.configure(state='normal');self.status.set('构建停止：'+data)
                    messagebox.showerror('提示',data)
        except queue.Empty:pass
        self.root.after(150,self.poll)

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
