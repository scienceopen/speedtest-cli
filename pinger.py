#!/usr/bin/env python
"""
Does speedtest every N minutes.
Perhaps useful for drive-testing in associtation with a GPS logging position every N minutes.
"""
import socket
import time
from speedtest_cli.speedtest_cli import speedtest,estimatespeed
from speedtest_cli.parsecmd import parsecmd


from argparse import ArgumentParser
p = ArgumentParser()
p.add_argument('-T','--period',help='do speedtest every T minutes',type=float,default=5)
p.add_argument('-o','--outfn',help='filename to write (default to tempdir)',default=time.strftime("%Y%m%d-%H%M%S") +'.log')
p = p.parse_args()

args = parsecmd()


outfn=p.outfn
#outfn = p.outfn[0]

print('logging to {}'.format(outfn))

with open(outfn,'w') as f:
    f.write('unixtime(sec) download(kB/sec) upload(kB/sec) ping(ms)\n')

done = False
while not done:
    try:
       dlspeedk,ulspeedk,ping,best = speedtest()
       done=True
    except socket.timeout:
       time.sleep(10)

while True:
    try:
       dlspeedk,ulspeedk,ping = estimatespeed(args,best)
       out='{:.1f} {:.0f} {:.0f} {:.1f}\n'.format(time.time(),dlspeedk,ulspeedk,ping)
    except (socket.timeout,RuntimeError):
        out = '{:.1f} 0 0 0\n'.format(time.time())

    with open(outfn,'a') as f:
        f.write(out)

    time.sleep(p.period * 60.)

