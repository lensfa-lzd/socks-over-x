# socks-over-x
使用第三方协议传输socks连接（新人练手项目，大佬勿喷）



## 用法

目前，客户端会使用一个本地端口，提供兼容socks5的代理服务，暂时实现了TCP协议。

### 从打包的可执行文件开始

使用pyinstaller库打包

1. 从release下载对应平台的压缩包并解压

2. 修改配置文件

   ```
   # 选择协议对应的文件夹，如websocket
   # 修改example_cfg.yaml中的内容，并重命名为config.yaml
   # local部分对应本地的配置，remote部分为服务器的配置
   
   local:
       server_port: 8000
       buffer: 16 # 单次数据包的最大大小：Kb
       client_id: 'sox-any'
       websocket_uris: # 可以选择一组连接地址，客户端会在每次连接时，随机选择地址
           [
               "ws://127.0.0.1/handle_websocket/",
               "ws://[::1]/handle_websocket/",
               "ws://example.com/handle_websocket/"
           ]
       retry: 1 # websocket断线重连尝试次数
       time_out: 60 # 睡眠模式等待时间：秒
       network: # 控制网络栈, 这里是指客户端会尝试以那种网络连接服务端
           [4, 6]
       log_level: 'warning'
   
   remote:
       buffer: 16 # 单次数据包的最大大小Kb
       websocket_port: 9000
       network: # 控制网络栈, 这里是指服务端会尝试以那种网络连接需要代理的网站
           [4, 6]
       log_level: 'warning'
       clients: # 允许的客户端
           ['sox']
   ```

3. 运行可执行文件

   ```
   # linux平台需要赋予可执行权限
   chomd +x server/client
   
   # 可手动指定配置文件位置，默认情况下使用相同目录的config.yaml文件
   ./server --config.yaml
   ```
   
### 从源代码开始

1. 安装依赖

   ```
   # 建议python 3.7+
   # uvloop是可选安装项，不支持windows平台
   pip install -r requirements.txt
   ```

2. 修改配置文件

   ```
   # 选择协议对应的文件夹，如websocket
   # 修改example_cfg.yaml中的内容，并重命名为config.yaml
   # local部分对应本地的配置，remote部分为服务器的配置
   
   local:
       server_port: 8000
       buffer: 16 # 单次数据包的最大大小：Kb
       client_id: 'sox-any'
       websocket_uris: # 可以选择一组连接地址，客户端会在每次连接时，随机选择地址
           [
               "ws://127.0.0.1/handle_websocket/",
               "ws://[::1]/handle_websocket/",
               "ws://example.com/handle_websocket/"
           ]
       retry: 1 # websocket断线重连尝试次数
       time_out: 60 # 睡眠模式等待时间：秒
       network: # 控制网络栈, 这里是指客户端会尝试以那种网络连接服务端
           [4, 6]
       log_level: 'warning'
   
   remote:
       buffer: 16 # 单次数据包的最大大小Kb
       websocket_port: 9000
       network: # 控制网络栈, 这里是指服务端会尝试以那种网络连接需要代理的网站
           [4, 6]
       log_level: 'warning'
       clients: # 允许的客户端
           ['sox']
   ```

3. 运行py脚本文件

   ```
   # 客户端
   python client.py
   
   # 服务端
   python server.py
   ```

   

## 设计理念

- 选择支持或者有可能被支持的CDN加速的协议进行通讯

  一个优秀的CDN可以大幅优化连接速度，而且笔者觉得直接使用点对点的传输方式和使用同一个域名进行连接的方式，特别是少数人使用的情况下，特征过于明显。只要稍微统计一下历史访问记录，很容易就能找出代理节点。

  CDN可以在一定程度上减轻这个问题，通过不断更换CDN提供的域名，以及利用CDN多IP的方式。

  目前大厂商的CDN基本都支持websocket回源，有限支持http/2回源（待测试），基本不支持http/3回源。

  2023/3/20

- 多路复用支持

  虽然有不少代理软件都支持多路复用功能，但是一般来说默认都是不开启的，[理由是在视频、下载或者测速通常会有反作用等](https://www.v2ray.com/chapter_02/mux.html)。笔者对于这些软件多路复用功能的实现路径还不是很明确，这里先按下不表，先以不开启多路复用的情形进行描述。
  
  在这类代理软件中，当有连接请求时，会向服务器端建立TCP通道，随即转发请求，当连接结束后就关闭TCP通道。对于每一个连接请求，都会开辟一条独立的TCP通道，并在请求结束之后关闭该通道。
  
  乍一看这样做并没有什么问题，同时还可以并行地建立多条TCP通道进行请求，并且能避免多路复用中TCP队头阻塞的问题。但是当使用的伪装协议是websocket，gRPC等协议时就会有问题了，因为websocket，gRPC等协议为了减少多个TCP连接的握手时间，设计时采用的是一个客户端保持一条连接的理念。也就是说，一个客户端与服务器之间会一直保持着一个TCP连接，直到数据传输结束。这样一看，不断地向同一个服务器反复地建立和断开websocket，gRPC等连接就有些鹤立鸡群了。
  
  于是，本项目采用了另一种方式构建隧道，进行多路复用传输。当有连接请求时，会向服务器端建立TCP通道，随即转发请求。但是当请求结束之后并不会马上关闭TCP通道，而是内置一个空闲时间。如果说在空闲时间之内又有连接请求，就会复用已经建立的TCP通道。当在一个空闲时间之内没有连接请求后，客户端就会进入“睡眠”状态，主动断开TCP通道。这样来模拟“正常”的通信场景。
  
  <img src="imgs\fig1.png" style="zoom:60%;" />
  
  2023/3/20

## 任务
- [x] websocket 协议

- [ ] http/2 协议

- [ ] http/3 协议
