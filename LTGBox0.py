#!/usr/bin/env python
# coding=utf-8
import configparser
import os
import sys
import time
import datetime
import sqlite3
import requests
import json
import playlistdb
import uuid
import _thread
import schedule
import dlnap
import socket
import urllib.parse
from urllib.request import urlopen
from flask import Flask, url_for,jsonify
from flask import request,Response
from flask_cors import CORS
app = Flask(__name__)
CORS(app)
from sqlalchemy import and_,or_,desc,asc

# 初始化工作，获取配置
shopDevices = []
#deletedDevices = []
playlistURI = ''
resourceHost = ''
localHttpHost = ''
useLocalHost = False
localHttpPort = '8001'
pycmd = 'python'
_sn_ = '000'
_version_ = '0.1.0'
_configfile_ = 'ltgbox.conf'
config = configparser.ConfigParser()

def initConfig():
    config["server"] = {
        'discover_uri':'',
        'playlist_uri':'',
        'resource_host':'',
        'localhttphost':'127.0.0.1',
        'localhttpport':'8001',
        'uselocalhost':'False',
        'pycmd' :'python3'
    }
    config["device"]={
        'sn' :'',
        'skey':'',
        'mkey':''
    }
    config["players"]={
        'BoxAudioCard' : '{"name": "BoxAudioCard", "host": "127.0.0.1", "type": "Audio", "protocol": "AudioCard", "state": "On", "path": ["/"]}'
    }
    with open(_configfile_, 'w') as configfile:
        config.write(configfile)

if os.path.exists(_configfile_) == False:
    initConfig()

#载入配置。
def loadConfig():
    print("载入配置")
    config.read(_configfile_)
    global _sn_  
    _sn_ = config.get("device","sn")
    global playlistURI
    playlistURI = config.get("server","playlist_uri")
    global resourceHost
    resourceHost = config.get("server","resource_host")
    global useLocalHost
    useLocalHost = config.get("server","useLocalHost") == "True"
    global localHttpHost
    if useLocalHost:
        localHttpHost = config.get("server","localHttpHost")
    else:
        try:
            hostname = socket.gethostname()    
            IPAddr = socket.gethostbyname(hostname)
            localHttpHost = IPAddr 
        except:
            localHttpHost = config.get("server","localHttpHost")
    global localHttpPort
    useLocalHost = config.get("server","localHttpPort")
    global pycmd
    pycmd = config.get("server","pycmd")
    global shopDevices
    for player in config.items('players'):
        playerconfig = json.loads(player[1])
        shopDevices.append(playerconfig)

#定时检查DLNA设备
def scanDLNADevices():
    os.system(pycmd + " ./dlnap.py")

if os.path.exists('./resources') == False:
    os.mkdir('resources')

#保存配置
def savePlayersConfig():
    config.read(_configfile_)
    config.remove_section("players")
    config.add_section("players")
    for dinfo in shopDevices:
        config.set("players",dinfo["name"],json.dumps(dinfo))
    with open(_configfile_, 'w') as f:
        config.write(f)

def resourceItemWorker(iotPath,resourceList):
    session = playlistdb.GetDbSession()
    idlist = []
    for item in resourceList:
        item_id = item["id"]
        idlist.append(item_id)
        item_filename = item["filename"]
        item_mediatype = item["mediatype"]
        item_duration = item["duration"]
        item_size = item["size"]
        item_tag = item["tag"]
        item_path = item["path"]
        _ ,item_filename_ext = os.path.splitext(item_filename)
        try:
            existitem = (session.query(playlistdb.PlayList)
                                .filter(playlistdb.PlayList.mediaid == item_id)
                                .first())
            if existitem == None :
                newplaylistid = uuid.uuid1().hex
                newplaylistRow = playlistdb.PlayList(playlistid=newplaylistid,
                           iotpath = iotPath,mediaid=item_id,
                           filename = item_filename,extension= item_filename_ext,
                           createdon = datetime.datetime.now(),tag = item_tag,
                           modifiedon = datetime.datetime.now(),urlpath = item_path,
                           status = 10,playcount=0,mediatype = item_mediatype,
                           size = item_size ,duration = item_duration)
                session.add(newplaylistRow)
                session.commit()
            else:
                if existitem.status == 2:
                    existitem.status = 10
                elif existitem.status in (1,20):
                    existitem.status = 0
                session.commit()
                print("资源" + item_filename + "(" + item_id + ")已注册")
        except:
            print("资源验证失败：" + item_filename + "(" + item_id + ")")
    return idlist
    

#处理获取到的播放列表。
def playPlanWorker(playlistPlan):
    #检查是否已存在该资源
    item_iotpath = playlistPlan["iotpath"]
    print("处理路径" + item_iotpath + "的资源。。。")
    test = False
    for device in shopDevices:
        for selfIoTPath in device["path"]:
            if selfIoTPath == '' :
                continue
            if selfIoTPath != item_iotpath :
                continue 
            test = True
            break
        if test:
            break
    if not test :
        return
    playlistData = playlistPlan["playlist"]
    playlistIds = resourceItemWorker(item_iotpath,playlistData)
    print("路径" + item_iotpath + "的资源处理完成")
    return playlistIds

#检查播入列表更新。
def checkPlayList(): 
    print("获取资源数据，资源地址："+playlistURI)
    if playlistURI == '':
        print("未配置资料主机地址。")
        return
    #注册新文件
    try:
        confRequest = requests.get(playlistURI)
    except:
        print("无法获取播放资源")
        return
    playlistIds = []
    if confRequest.status_code == 200 :
        print("资源单获取成功，进行验证")
        jdata = json.loads(confRequest.text)
        for i in jdata:
            pfiles = playPlanWorker(i)
            if pfiles != None :
                playlistIds = playlistIds + pfiles
    else:
        print("资源单获取失败")
    #处理已删除文件
    session = playlistdb.GetDbSession()
    notdelFiles = (session.query(playlistdb.PlayList)
                            .filter(playlistdb.PlayList.status != 20))
    nflist = []
    for nf in notdelFiles:
        nflist.append(nf.mediaid)
    diffFiles = list(set(nflist).difference(set(playlistIds)))
    isDirty = False
    for nf in notdelFiles:
        if nf.mediaid in diffFiles:
            print(nf.filename + "文件标记为删除")
            nf.status = 20
            isDirty = True
    if isDirty:
        session.commit()
    diffFiles = list(set(playlistIds).difference(set(nflist)))
    print("资源检查完成")
        
# 下载数据库中未下载的资源
def downloadResource():
    print("查找需要下载的资源")
    session2 = playlistdb.GetDbSession()
    playlistTarget = (session2.query(playlistdb.PlayList)
        .filter(or_(playlistdb.PlayList.status == 10,playlistdb.PlayList.status == 11))
        .first())
    if playlistTarget != None :
        print("资源" + playlistTarget.filename + "准备下载中...")
        url = resourceHost + urllib.parse.quote(playlistTarget.urlpath) + urllib.parse.quote(playlistTarget.filename)
        #本地文件夹
        localPath = sys.path[0] + "/resources" + playlistTarget.iotpath
        if os.path.exists(localPath) == False:
            os.makedirs(localPath)
        #本地文件路径
        localFile = localPath + playlistTarget.playlistid + playlistTarget.extension
        #检查本地文件是否已存在,如果存在则无需下载
        if os.path.exists(localFile) :
            finfo = os.stat(localFile)
            if finfo.st_size == playlistTarget.size :
                print("文件" + playlistTarget.filename + "已存在，无需下载")
                playlistTarget.status = 0
                playlistTarget.modifiedon = datetime.datetime.now()
                session2.commit()
                _thread.start_new_thread(downloadResource,())
                return
            else:
                #处理未下载完成的任务
                try:
                    os.remove(localFile)
                except:
                    print("文件" + localFile + "已存在，下载未完成，但无法访问。")
                    time.sleep(5)
                    _thread.start_new_thread(downloadResource,())
                    return
        
        if playlistTarget.status != 11:
            playlistTarget.status = 11
            playlistTarget.modifiedon = datetime.datetime.now()
            session2.commit()
        print("资源下载" + playlistTarget.filename + ".请求：" + url)
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(localFile,"wb") as wfile:
                for chunk in response.iter_content(chunk_size=1024 * 8):
                    if chunk:
                        wfile.write(chunk)
                wfile.close()
        except:
            print("资源" + playlistTarget.filename + "下载发生错误")
            playlistTarget.status = 10
            playlistTarget.modifiedon = datetime.datetime.now()
            _thread.start_new_thread(downloadResource,())
            return
        finfo = os.stat(localFile)
        if finfo.st_size == playlistTarget.size :
            print("资源" + playlistTarget.filename + "下载完成")
            playlistTarget.status = 0
            playlistTarget.modifiedon = datetime.datetime.now()
        else:
            print("资源" + playlistTarget.filename + "下载失败")
            os.remove(localFile)
            playlistTarget.status = 10
            playlistTarget.modifiedon = datetime.datetime.now()
        session2.commit()
        time.sleep(1)
        _thread.start_new_thread(downloadResource,())
    else:  
        print("没有需要下载的资源")
        time.sleep(60)
        _thread.start_new_thread(downloadResource,())

# 播放MP3       
def playMusic(audiocard,filename):
    if audiocard is None or audiocard=='':
        os.system('mpg321 "'+filename+'"') 
    else:
        os.system('mpg321 -o alsa -a '+audiocard +' "'+filename+'"') 

def getDevicePlaylist(deviceInfo):
    session3 = playlistdb.GetDbSession()
    playlist = []
    if deviceInfo["type"] == "Video" :
        playlist = (session3.query(playlistdb.PlayList)
            .filter(playlistdb.PlayList.status == 0)
            .filter(or_(playlistdb.PlayList.mediatype == "Video",playlistdb.PlayList.mediatype == "Image"))
            .filter(playlistdb.PlayList.iotpath.in_(deviceInfo["path"]))
            .order_by(asc(playlistdb.PlayList.lastplaytime)))
    elif deviceInfo["type"] == "Audio" :
        playlist = (session3.query(playlistdb.PlayList)
            .filter(playlistdb.PlayList.status == 0)
            .filter(playlistdb.PlayList.mediatype == "Audio")
            .filter(playlistdb.PlayList.iotpath.in_(deviceInfo["path"]))
            .order_by(asc(playlistdb.PlayList.lastplaytime)))
    return playlist
    
#设备播放线程
def playMediaWorker(deviceHost):
    deviceInfo = None
    for dev in shopDevices:
        if dev["host"] == deviceHost:
            deviceInfo = dev
    if deviceInfo == None or deviceInfo["host"] != deviceHost:
        return
    if deviceInfo["state"] != "On":
        print( deviceInfo["name"]+"设备已停用。")
        return
    print("获取设备" + deviceInfo["host"] + "的播放列表")
    session3 = playlistdb.GetDbSession()
    mediafile = None
    if deviceInfo["type"] == "Video" :
        mediafile = (session3.query(playlistdb.PlayList)
            .filter(playlistdb.PlayList.status == 0)
            .filter(or_(playlistdb.PlayList.mediatype == "Video",playlistdb.PlayList.mediatype == "Image"))
            .filter(playlistdb.PlayList.iotpath.in_(deviceInfo["path"]))
            .order_by(asc(playlistdb.PlayList.lastplaytime))
            .first())
    elif deviceInfo["type"] == "Audio" :
        mediafile = (session3.query(playlistdb.PlayList)
            .filter(playlistdb.PlayList.status == 0)
            .filter(playlistdb.PlayList.mediatype == "Audio")
            .filter(playlistdb.PlayList.iotpath.in_(deviceInfo["path"]))
            .order_by(asc(playlistdb.PlayList.lastplaytime))
            .first())
    
    if mediafile == None :
        print("设备" + deviceInfo["host"] + "无可播放的媒体资源")
        time.sleep(30)
        _thread.start_new_thread(playMediaWorker,(deviceHost,))
        return
    threadDuration = mediafile.duration / 1000 - 1
    if threadDuration < 0:
        threadDuration = 0

    print("播放媒体文件" + mediafile.filename + "至" + deviceInfo["host"] + ",执行时间：" + str(threadDuration) + "秒")
    if deviceInfo["protocol"] == "DLNA":
        localfilename ="http://" +localHttpHost +":" +localHttpPort +mediafile.iotpath + mediafile.playlistid + mediafile.extension
        playCmd = pycmd + " ./dlnap.py --ip " + deviceInfo["host"] + " --play '" + localfilename + "'"
        print("执行：" + playCmd)
        os.system(playCmd)
    elif deviceInfo["protocol"] == "AudioCard":
        threadDuration +=2
        localfilename = "resources"+mediafile.iotpath + mediafile.playlistid + mediafile.extension
        print("本机声卡播放："+localfilename)
        _thread.start_new_thread(playMusic,(deviceInfo["host"], localfilename))
    else:
        pass
    mediafile.lastplaytime = datetime.datetime.now()
    mediafile.playcount = mediafile.playcount+1
    mediafile.modifiedon = datetime.datetime.now()
    session3.commit()
    time.sleep(threadDuration)
    _thread.start_new_thread(playMediaWorker,(deviceHost,))


def iot_alive_report():
    deviceSN = config.get("device","sn")
    try:
        hostname = socket.gethostname()    
        IPAddr = socket.gethostbyname(hostname)
    except:
        print('心跳报告,获取主机IP失败。')
        return
    aliveInfo ={
        'skey' : config.get("device","skey"),
        'lan_ip' :IPAddr
    }
    reqUrl = config.get('server','discover_uri')+'/iot/alive/'+deviceSN
    try:
        response = requests.post(reqUrl,data=aliveInfo)
        print('完成报告。')
    except:
        print('心跳报告失败。')
    return

def thread_checkPlayList():
    _thread.start_new_thread(checkPlayList,())

def thread_iot_aliveReport():
    _thread.start_new_thread(iot_alive_report,())

def thread_scanDLNADevices():
    _thread.start_new_thread(scanDLNADevices,())
 
def BackgroupTask():

    thread_checkPlayList()
    schedule.every(20).seconds.do(thread_checkPlayList)
    thread_iot_aliveReport()
    schedule.every(30).seconds.do(thread_iot_aliveReport)
    thread_scanDLNADevices()
    schedule.every(5).minutes.do(thread_scanDLNADevices)
    _thread.start_new_thread(downloadResource,())
    for d in shopDevices:
        if d["state"] == "On":
            _thread.start_new_thread(playMediaWorker,(d["host"],))
    while True:
        schedule.run_pending()
        time.sleep(1)

	
#获取程序名称和版本号
@app.route('/',methods = ['GET'])
def api_root():
    boxInfo =  {'name' :'LTG ShopMBox','sn':_sn_,'version':_version_}
    return jsonify(boxInfo)

#查找DLNA设备
@app.route('/api/device/findDLNADevices',methods = ['GET'])
def api_device_findDLNADevices():
    try:
        dlist = dlnap.discover(timeout=3)
    except:
        return Response(json.dumps([]))
    devInfos = []
    for dinfo in dlist:
        ditem = {
            'name':dinfo.name,
            'ip':dinfo.ip
        }
        devInfos.append(ditem)
    return json.dumps(devInfos)

#查出所有已注册的设备
@app.route('/api/device/all',methods = ['GET','POST'])
def api_device_all():
    global shopDevices
    if request.method == 'POST' :
        postData = request.data.decode()
        postDevices = json.loads(postData)["devices"]
        newDev = []
        stopDevices = []
        deletedDevices = []
        #处理新增设备或被重新开启的设备
        for p in postDevices:
            existed = False
            for o in shopDevices:
                if o["host"] == p["host"]:
                    existed = True
                    if o["state"] == "On" and p["state"] != "On":
                        o["state"] = "Off"
                        deletedDevices.append(o)
                    elif o["state"] != "On" and p["state"] == "On":
                        newDev.append(p)
                    break
            if not existed:
                newDev.append(p)
        #处理被删除的设备，将它列和已删除设备。
        for o in shopDevices:
            existed = False
            for p in postDevices:
                if p["host"] == o["host"]:
                    existed = True
                    break
            if not existed:
                deletedDevices.append(o)
        #为新增设备启动播放线程
        for d in newDev:
            if d["state"] == "On":
                _thread.start_new_thread(playMediaWorker,(d["host"],))
        shopDevices = postDevices
        savePlayersConfig()
        #将删除设备停止播放
        for d in deletedDevices:
            if d["type"] == "Video" and d["protocol"]=="DLNA":
                playCmd = pycmd + " ./dlnap.py --ip " + d["host"] + " --stop"
                print("执行：" + playCmd)
                os.system(playCmd)
        return Response(status=200) 
    elif request.method == 'GET':
        return json.dumps(shopDevices)


#获取指定节点的配置
@app.route('/api/ltgbox/config/server',methods = ['GET'])
def api_ltgbox_config_node():
    nodesConfig = config.items('server')
    nodesObj = {}
    for n in nodesConfig:
        nodesObj[n[0]]=n[1]
    return json.dumps(nodesObj)

def runWebApp():
    app.run(host='0.0.0.0', port=5604)


if __name__ == '__main__':
    #配置初始化
    loadConfig()
    scanDLNADevices()
    _thread.start_new_thread(BackgroupTask,())
    _thread.start_new_thread(runWebApp,())
    while True:
        time.sleep(1)
        pass
