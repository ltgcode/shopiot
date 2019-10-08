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
import signal
import urllib.parse
from urllib.request import urlopen
from flask import Flask, url_for,jsonify
from flask import request,Response
from flask_cors import CORS
from urllib.parse import quote
app = Flask(__name__)
CORS(app)
from sqlalchemy import and_,or_,desc,asc
import logging
import logging.config

#常量
_SN_ = '000'
_VERSION_ = '0.2.2.0'
_CONFIGFILE_ = 'ltgbox.conf'
_LAST_UPDATE_ = 'update.txt'
DEFAULT_DRIVE = "./device/default.json"

# 初始化工作，获取配置
DiscoverURI = ''
ShopDevices= []
PlaylistURI = ''
ResourceHost = ''
LocalHttpHost = ''
UseLocalHost = False
LocalHttpPort = '8001'
PyCmd = 'python'
Config = configparser.ConfigParser()
SysUpdating = False
AppStopAction = "None"
NoADUntil = {}
PlayListSet = {}

#
# Signal of Ctrl+C
# =================================================================================================
def signal_handler(signal, frame):
    global AppStopAction
    AppStopAction = "Close"
    logger.info(' Got Ctrl + C, exit now!')
    sys.exit(1)

signal.signal(signal.SIGINT, signal_handler)

#必要的目录
if os.path.exists('./resources') == False:
    os.mkdir('resources')

if os.path.exists('./log') == False:
    os.mkdir('log')

def resetUpdateCheckCode():
    with open(_LAST_UPDATE_,'w') as cf:
        cf.writelines('0')

if not os.path.exists(_LAST_UPDATE_):
    resetUpdateCheckCode()

logging.config.fileConfig(fname='logger.conf', disable_existing_loggers=False)
logger = logging.getLogger("mainlog")

def initConfig():
    Config["server"] = {
        'discover_uri':'',
        'playlist_uri':'',
        'resource_host':'',
        'localhttphost':'127.0.0.1',
        'localhttpport':'8001',
        'uselocalhost':'False',
        'pycmd' :'python3'
    }
    Config["device"]={
        'sn' :'',
        'skey':'',
        'mkey':''
    }
    Config["players"]={
        'BoxAudioCard' : '{"name": "BoxAudioCard", "host": "127.0.0.1", "type": "Audio", "protocol": "AudioCard", "state": "On", "path": ["/"]}'
    }
    with open(_CONFIGFILE_, 'w') as configfile:
        Config.write(configfile)

if os.path.exists(_CONFIGFILE_) == False:
    initConfig()

def getHostIP():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip

#载入配置。
def loadConfig():
    #载入配置
    logger.info("载入配置")
    Config.read(_CONFIGFILE_)
    global DiscoverURI
    DiscoverURI = Config.get("server","discover_uri")
    global _SN_  
    _SN_ = Config.get("device","sn")
    global PlaylistURI
    PlaylistURI = Config.get("server","playlist_uri")
    global ResourceHost
    ResourceHost = Config.get("server","resource_host")
    global UseLocalHost
    UseLocalHost = Config.get("server","useLocalHost") == "True"
    global LocalHttpHost
    if UseLocalHost:
        LocalHttpHost = Config.get("server","localHttpHost")
    else:
        try:
            LocalHttpHost = getHostIP() 
        except:
            LocalHttpHost = Config.get("server","localHttpHost")
    global LocalHttpPort
    UseLocalHost = Config.get("server","localHttpPort")
    #Phthon命令
    global PyCmd
    PyCmd = Config.get("server","pycmd")
    #清掉标记文件
    resetUpdateCheckCode()
    #初始扫描设备
    global ShopDevices
    for player in Config.items('players'):
        playerconfig = json.loads(player[1])
        ShopDevices.append(playerconfig)

#定时检查DLNA设备
def scanDLNADevices():
    os.system(PyCmd + " ./dlnap.py")

#修正设备IP地址
def fixDevices():
    try:
        dlist = dlnap.discover(timeout=20)
    except Exception as err:
        logger.error("自动修复设备配置失败,%s",err)
        return 
    changed = False
    for sd in ShopDevices:
        for dinfo in dlist:
            if dinfo.name == sd["name"] and dinfo.ip != sd["host"]:
                sd["host"] = dinfo.ip
                changed = True
            if dinfo.name != sd["name"] and dinfo.ip == sd["host"]:
                sd["name"]  = dinfo.name
                changed = True
    if changed :
        savePlayersConfig()
    

#保存配置
def savePlayersConfig():
    Config.read(_CONFIGFILE_)
    Config.remove_section("players")
    Config.add_section("players")
    for dinfo in ShopDevices:
        Config.set("players",dinfo["name"],json.dumps(dinfo))
    with open(_CONFIGFILE_, 'w') as f:
        Config.write(f)

#处理播放列表项
def resourceItemWorker(iotPath,resourceList):
    session = playlistdb.GetDbSession()
    idlist = []
    for item in resourceList:
        idlist.append(item["id"])
        _ ,item_filename_ext = os.path.splitext(item["filename"])
        try:
            existitem = (session.query(playlistdb.PlayList)
                                .filter(playlistdb.PlayList.mediaid == item["id"])
                                .first())
            if existitem == None :
                newplaylistid = uuid.uuid1().hex
                newplaylistRow = playlistdb.PlayList(playlistid=newplaylistid,
                           iotpath = iotPath,mediaid=item["id"],
                           filename = item["filename"],extension= item_filename_ext,
                           createdon = datetime.datetime.now(),tag = item["tag"],
                           modifiedon = datetime.datetime.now(),urlpath = item["path"],
                           status = 10,playcount=0,mediatype = item["mediatype"],
                           size = item["size"] ,duration = item["duration"])
                session.add(newplaylistRow)
                session.commit()
            else:
                if existitem.filename != item["filename"]:
                    existitem.filename = item["filename"]
                if existitem.urlpath != item["path"]:
                    existitem.urlpath = item["path"]
                if existitem.iotpath != iotPath:
                    existitem.iotpath = iotPath
                if existitem.tag != item["tag"]:
                    existitem.tag = item["tag"]
                # status 状态：0 启用，1停用,2,已删除，20，待删除,10：待启用，11：准备中,
                if existitem.status == 2:
                    existitem.status = 10
                elif existitem.status in (1,20):
                    existitem.status = 0
                if existitem.status == 0:
                     #本地文件夹
                    localPath = sys.path[0] + "/resources/"
                    #本地文件路径
                    localFile = localPath + existitem.mediaid + existitem.extension
                    #检查本地文件是否已存在,如果存在则无需下载
                    if not os.path.exists(localFile) :
                        existitem.status = 10
                session.commit()
                logger.info("资源" + item["filename"] + "(" + item["id"] + ")已注册")
        except Exception as err:
            logger.warning("资源验证失败：" + item["filename"] + "(" + item["id"] + "),%s",err)
    return idlist
    

#处理获取到的播放路径
def playPlanWorker(playlistPlan):
    #检查是否已存在该资源
    item_iotpath = playlistPlan["iotpath"]
    logger.info("处理路径" + item_iotpath + "的资源。。。")
    test = False
    for device in ShopDevices:
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
    logger.info("路径" + item_iotpath + "的资源处理完成")
    return playlistIds

#检查播入列表更新。
def checkPlayList(): 
    if checkAppStopAction():
        return
    #检查是否有更新
    checkFileURI = PlaylistURI+'.txt'
    try:
        checkRequest = requests.get(checkFileURI)
    except Exception as err:
        logger.error("无法获取更新标记文本信息，请检查网络.%s",err)
        return
    if checkRequest.status_code == 200:
        checkCode = checkRequest.text
    else:
        logger.error("请求媒体更新标记失败")
    with open(_LAST_UPDATE_,'r') as cf:
        localCheckCode = cf.read()
    if localCheckCode == checkCode:
        logger.info("媒体列表未发现更新。")
        return
    #获取媒体列表
    logger.info("获取资源数据，资源地址："+PlaylistURI)
    if PlaylistURI == '':
        logger.warning("未配置资料主机地址。")
        return
    #注册新文件
    try:
        confRequest = requests.get(PlaylistURI)
    except Exception as err:
        logger.error("无法获取播放资源.%s",err)
        return
    playlistIds = []
    if confRequest.status_code == 200 :
        logger.info("资源单获取成功，进行验证")
        jdata = json.loads(confRequest.text)
        for i in jdata:
            pfiles = playPlanWorker(i)
            if pfiles != None :
                playlistIds = playlistIds + pfiles
    else:
        logger.warning("资源单获取失败")

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
            logger.info(nf.filename + "文件标记为删除")
            nf.status = 20
            isDirty = True
    if isDirty:
        session.commit()
    diffFiles = list(set(playlistIds).difference(set(nflist)))
    loadPlaylist()
    with open(_LAST_UPDATE_,'w') as cf:
        cf.writelines(checkCode)
    logger.info("资源检查完成")

def checkAppStopAction():
    if AppStopAction in ("Restart","Close"):
        return True
    else:
        return False 
        
# 下载数据库中未下载的资源
def downloadResource():
    #处理重启情况
    if checkAppStopAction():
        return
    logger.info("查找需要下载的资源")
    session2 = playlistdb.GetDbSession()
    playlistTarget = (session2.query(playlistdb.PlayList)
        .filter(or_(playlistdb.PlayList.status == 10,playlistdb.PlayList.status == 11))
        .first())
    if playlistTarget != None :
        logger.info("资源" + playlistTarget.filename + "准备下载中...")
        url = ResourceHost + quote(playlistTarget.urlpath) + quote(playlistTarget.filename)
        #本地文件夹
        localPath = sys.path[0] + "/resources/"
        #本地文件路径
        localFile = localPath + playlistTarget.mediaid + playlistTarget.extension
        #检查本地文件是否已存在,如果存在则无需下载
        if os.path.exists(localFile) :
            finfo = os.stat(localFile)
            if finfo.st_size == playlistTarget.size :
                logger.info("文件" + playlistTarget.filename + "已存在，无需下载")
                playlistTarget.status = 0
                playlistTarget.modifiedon = datetime.datetime.now()
                session2.commit()
                _thread.start_new_thread(downloadResource,())
                return
            else:
                #处理未下载完成的任务
                try:
                    os.remove(localFile)
                except Exception as err:
                    logger.warning("文件" + localFile + "已存在，下载未完成，但无法访问。%s",err)
                    time.sleep(5)
                    _thread.start_new_thread(downloadResource,())
                    return
        
        if playlistTarget.status != 11:
            playlistTarget.status = 11
            playlistTarget.modifiedon = datetime.datetime.now()
            session2.commit()
        logger.info("资源下载" + playlistTarget.filename + ".请求：" + url)
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(localFile,"wb") as wfile:
                for chunk in response.iter_content(chunk_size=1024 * 8):
                    if chunk:
                        wfile.write(chunk)
                wfile.close()
        except Exception as err:
            logger.error("资源" + playlistTarget.filename + "下载发生错误.%s",err)
            playlistTarget.status = 10
            playlistTarget.modifiedon = datetime.datetime.now()
            time.sleep(5)
            _thread.start_new_thread(downloadResource,())
            return
        finfo = os.stat(localFile)
        if finfo.st_size == playlistTarget.size :
            logger.info("资源" + playlistTarget.filename + "下载完成")
            playlistTarget.status = 0
            playlistTarget.modifiedon = datetime.datetime.now()
            loadPlaylist()
        else:
            logger.error("资源" + playlistTarget.filename + "下载失败")
            os.remove(localFile)
            playlistTarget.status = 10
            time.sleep(1)
            playlistTarget.modifiedon = datetime.datetime.now()
        session2.commit()
        time.sleep(1)
        _thread.start_new_thread(downloadResource,())
    else:  
        logger.info("没有需要下载的资源")
        time.sleep(60)
        _thread.start_new_thread(downloadResource,())

# 播放MP3       
def playMusic(audiocard,filename):
    if checkAppStopAction():
        return
    if audiocard is None or audiocard=='':
        os.system('mpg321 "'+filename+'"') 
    else:
        os.system('mpg321 -o alsa -a '+audiocard +' "'+filename+'"') 

def playVedio(devname,filename):
    try:
        devinfo = dlnap.DlnapDevice(None,None)
        devinfo.loadByName(devname)
        time.sleep(2)
        devinfo.stop()
        resData = devinfo.set_current_media_s(filename)
        if resData == None :
            dlnap.discover()
        if resData.status_code != 200:
            devinfo.set_current_media(filename)
        devinfo.play()
    except Exception as err:
        logger.error("视频播放出现错误"+filename+".%s",err)

#清理资源文件
def removeResourceFiles():
    pass

#载入节目单
def loadPlaylist():
    if checkAppStopAction():
        return
    session = playlistdb.GetDbSession()
    global PlayListSet
    playlist = (session.query(playlistdb.PlayList)
            .filter(playlistdb.PlayList.status == 0)
            .order_by(playlistdb.PlayList.filename))
    for dev in ShopDevices:
        devPlaylist = None
        #获取当前的设备的播放列表
        if dev["name"] not in PlayListSet:
            PlayListSet[dev["name"]] = {'lastIndex':0,'playlist':[]}
        devPlaylist = PlayListSet[dev["name"]]
        newplaylist = []
        #遍历文件列表，获取需要播放的文件
        for mfile in playlist:
            if mfile.iotpath not in dev["path"]:
                continue
            if (dev["type"] == "Video" and mfile.mediatype not in ('Video','Image')) or \
                (dev["type"] == "Audio" and mfile.mediatype not in ('Audio')):
                continue
            newplaylist.append({
                'id':mfile.playlistid,
                'mediaid':mfile.mediaid,
                'filename':mfile.filename,
                'iotpath':mfile.iotpath,
                'extension':mfile.extension,
                'duration':mfile.duration})
        logger.info(dev["name"]+"节目单："+ json.dumps(newplaylist))
        devPlaylist["playlist"] = newplaylist

#获取设备要播放的下一个节目
def getNextMediaFile(devHost):
    if devHost not in PlayListSet:
        return
    devPlaylistInfo = PlayListSet[devHost]
    currIndex = devPlaylistInfo["lastIndex"]
    devPlaylist = devPlaylistInfo["playlist"]
    if len(devPlaylist) == 0:
        return None
    nextIndex = currIndex +1
    if nextIndex >= len(devPlaylist):
        nextIndex = 0
    devPlaylistInfo["lastIndex"] = nextIndex
    return devPlaylist[nextIndex]

#设备播放线程
def playMediaWorker(deviceHost):
    #检查是否到达禁播时间
    global NoADUntil
    if deviceHost in NoADUntil:
        noadtime = NoADUntil[deviceHost]
        if  datetime.datetime.now().timetuple() < noadtime:
            time.sleep(3)
            _thread.start_new_thread(playMediaWorker,(deviceHost,))
            return

    #处理重启情况
    if checkAppStopAction():
        return

    try:
        #获取设备信息
        deviceInfo = None
        for dev in ShopDevices:
            if dev["name"] == deviceHost:
                deviceInfo = dev
        if deviceInfo == None or deviceInfo["name"] != deviceHost:
            return
        if deviceInfo["state"] != "On":
            logger.warning( deviceInfo["name"]+"设备已停用。")
            return
        logger.info("获取设备" + deviceInfo["name"] + "的播放列表")
        
        mediafile = getNextMediaFile(deviceHost)
        if mediafile == None :
            logger.info("设备" + deviceInfo["name"] + "无可播放的媒体资源")
            time.sleep(30)
            _thread.start_new_thread(playMediaWorker,(deviceHost,))
            return
        threadDuration = mediafile["duration"] / 1000 - 1
        if threadDuration < 0:
            threadDuration = 0

        logger.info("播放媒体文件" + mediafile["filename"] + "至" + deviceInfo["name"] + ",执行时间：" + str(threadDuration) + "秒")
        if deviceInfo["protocol"] == "DLNA":
            threadDuration -= 2
            localfilename ="http://" +LocalHttpHost +":" +LocalHttpPort + "/"+ mediafile["mediaid"] + mediafile["extension"]
            logger.info("视频文件地址："+localfilename)
            _thread.start_new_thread(playVedio,(deviceInfo["name"] , localfilename))
        elif deviceInfo["protocol"] == "AudioCard":
            threadDuration +=2
            localfilename = "resources/"+ mediafile["mediaid"] + mediafile["extension"]
            logger.info("本机声卡播放："+localfilename)
            _thread.start_new_thread(playMusic,(deviceInfo["name"], localfilename))
        else:
            pass

        session = playlistdb.GetDbSession()
        targetRow = (session.query(playlistdb.PlayList)
                    .filter(playlistdb.PlayList.playlistid == mediafile["id"])
                    .first())
        if targetRow != None:
            targetRow.lastplaytime = datetime.datetime.now()
            targetRow.playcount = targetRow.playcount+1
            targetRow.modifiedon = datetime.datetime.now()
            session.commit()
        time.sleep(threadDuration)
        _thread.start_new_thread(playMediaWorker,(deviceHost,))
    except Exception as err:
        logger.error( deviceHost+ ",播放媒体出错。%s",err)
        time.sleep(5)
        _thread.start_new_thread(playMediaWorker,(deviceHost,))


def iot_alive_report():
    global LocalHttpHost
    try:
        LocalHttpHost = getHostIP()
    except Exception as err:
        logger.error('心跳报告,获取主机IP失败。%s',err)
        return
    devicesList = []
    with open(DEFAULT_DRIVE,'r') as dfile:
        ddata = dfile.read()
        jdevices = json.loads(ddata)
        for dinfo in jdevices:
            devbaseInfo = {'name':dinfo["name"],'ip':dinfo["ip"]}
            devicesList.append(devbaseInfo)
    aliveInfo ={
        'skey' : Config.get("device","skey"),
        'lan_ip' :LocalHttpHost,
        'version': _VERSION_,
        'devices': json.dumps( devicesList)
    }
    reqUrl = DiscoverURI+'/iot/alive/'+_SN_
    try:
        requests.post(reqUrl,data=aliveInfo)
        logger.info('完成报告。')
    except Exception as err:
        logger.error('心跳报告失败。%s',err)
    return

def thread_checkPlayList():
    _thread.start_new_thread(checkPlayList,())

def thread_iot_aliveReport():
    _thread.start_new_thread(iot_alive_report,())

def thread_scanDLNADevices():
    _thread.start_new_thread(scanDLNADevices,())

	
#获取程序名称和版本号
@app.route('/',methods = ['GET'])
def api_root():
    boxInfo =  {'name' :'LTG ShopMBox','sn':_SN_,'version':_VERSION_}
    return jsonify(boxInfo)

#查找DLNA设备
@app.route('/api/device/findDLNADevices',methods = ['GET'])
def api_device_findDLNADevices():
    try:
        dlist = dlnap.discover(timeout=10)
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

def playToDevice(devicename,url,endtime):
    device = dlnap.DlnapDevice(None,None)
    device.loadByName(devicename)
    tv = dlnap.DlnapDevice(device._DlnapDevice__raw.encode('utf-8'),device.ip)
    NoADUntil[devicename] = endtime
    tv.set_current_media(url)
    tv.play()

def updateRemoteCommandStatus(cmdid,status):
    global DiscoverURI
    reqUrl = DiscoverURI+'/iot/command/'+_SN_
    reqData = {
        'commandid':cmdid,
        'status':status
    }
    try:
        requests.put(reqUrl,data=reqData)
        logger.info('完成命令状态更新'+cmdid+":status-"+str(status))
    except Exception as err:
        logger.error('完成命令状态更新失败。%s',err)
    

def remoteCommandsRunner():
    global DiscoverURI
    reqUrl = DiscoverURI+'/iot/command/'+_SN_
    try:
        res = requests.get(reqUrl)
        commandObj = json.loads(res.text)
        if "command" not in commandObj:
            return
        command = commandObj["command"]
        cmdid = commandObj["id"]
        cmdStatus = commandObj["status"]
        if cmdStatus != 0:
            return
        if command == "Restart":
            updateRemoteCommandStatus(cmdid,'1')
            startLTGBoxApp()
        elif command =="UpdateApp":
            updateRemoteCommandStatus(cmdid,'1')
            updateDevice()
        elif command == "Play":
            cmddata = json.loads(commandObj["data"])
            endtime =time.strptime(cmddata["endtime"],'%Y-%m-%dT%H:%M:%S')
            playToDevice(cmddata["devicename"],cmddata["url"],endtime)
            updateRemoteCommandStatus(cmdid,'1')
    except Exception as err:
        logger.error('Remote command runner error.%s',err)
    pass

#查出所有已注册的设备
@app.route('/api/device/all',methods = ['GET','POST'])
def api_device_all():
    global ShopDevices
    if request.method == 'POST' :
        postData = request.data.decode()
        postDevices = json.loads(postData)["devices"]
        newDev = []
        deletedDevices = []
        #处理新增设备或被重新开启的设备
        for p in postDevices:
            existed = False
            for o in ShopDevices:
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
        for o in ShopDevices:
            existed = False
            for p in postDevices:
                if p["host"] == o["host"]:
                    existed = True
                    break
            if not existed:
                deletedDevices.append(o)
        ShopDevices = postDevices
        savePlayersConfig()
        #将删除设备停止播放
        for d in deletedDevices:
            if d["type"] == "Video" and d["protocol"]=="DLNA":
                playCmd = PyCmd + " ./dlnap.py --ip " + d["host"] + " --stop"
                logger.info("执行：" + playCmd)
                os.system(playCmd)
        resetUpdateCheckCode()
        checkPlayList()
        loadPlaylist()
        #为新增设备启动播放线程
        for d in newDev:
            if d["state"] == "On":
                _thread.start_new_thread(playMediaWorker,(d["name"],))
        return Response(status=200) 
    elif request.method == 'GET':
        return json.dumps(ShopDevices)


#获取指定节点的配置
@app.route('/api/ltgbox/config/server',methods = ['GET'])
def api_ltgbox_config_node():
    nodesConfig = Config.items('server')
    nodesObj = {}
    for n in nodesConfig:
        nodesObj[n[0]]=n[1]
    return json.dumps(nodesObj)

#重启应用
def startLTGBoxApp():
    global AppStopAction 
    if AppStopAction != "None":
        return
    logger.info('等待重启应用')
    AppStopAction = "Restart"
    logger.warning("应用重启中")
    restartbox = PyCmd+' LTGBox0.py'
    os.system(restartbox)

def updateDevice():
    global SysUpdating
    if SysUpdating:
        return json.dumps({"error":"Sys is updating"})
    try:
        SysUpdating = True
        gitPullCmd = 'git fetch --all ;git reset --hard ; git pull'
        os.system(gitPullCmd)
    finally:
        SysUpdating = False
    _thread.start_new_thread(startLTGBoxApp,())

@app.route('/api/ltgbox/restart',methods=['POST'])
def api_ltgbox_restart():
    _thread.start_new_thread(startLTGBoxApp,())
    return Response(status=200) 

#升级设备
@app.route('/api/ltgbox/update',methods=['POST'])
def device_software_update():
    logger.info("收到升级请求，开始执行升级")
    _thread.start_new_thread(updateDevice,())
    return Response(status=200) 


def runWebApp():
    time.sleep(30)
    app.run(host='0.0.0.0', port=5604)

def checkUpdate():
    _thread.start_new_thread(updateDevice,())


 
#启动所有异步线程
def BackgroupTask():
    loadPlaylist()
    #检查媒体资源列表
    thread_checkPlayList()
    schedule.every(180).seconds.do(thread_checkPlayList)
    #修正设备IP
    fixDevices()
    schedule.every(5).minutes.do(fixDevices)
    #心跳报告
    thread_iot_aliveReport()
    schedule.every(30).seconds.do(thread_iot_aliveReport)
    #远程命令
    schedule.every(10).seconds.do(remoteCommandsRunner)
    #DLNA设备查找
    thread_scanDLNADevices()
    schedule.every(10).minutes.do(thread_scanDLNADevices)
    #资源下载任务
    _thread.start_new_thread(downloadResource,())
    #定时检查升级
    schedule.every().day.at("02:00").do(checkUpdate)
    for d in ShopDevices:
        if d["state"] == "On":
            _thread.start_new_thread(playMediaWorker,(d["name"],))
            pass
    global AppStopAction
    while AppStopAction == "None":
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    #配置初始化
    try:
        loadConfig()
        scanDLNADevices()
        _thread.start_new_thread(BackgroupTask,())
        _thread.start_new_thread(runWebApp,())
        while AppStopAction == "None":
            time.sleep(1)
        logger.info("应用将在10秒后关闭")
        time.sleep(10)
    except Exception as err:
        logger.error("应用发生错误，程序中断.%s",err)
