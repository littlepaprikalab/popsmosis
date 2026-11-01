#!/bin/bash
# sheet.sh <video> <out.png> [every_s] [cols]: a contact sheet of frames every N seconds, each stamped with its time.
set -eu
video=$1; out=$2; every=${3:-6}; cols=${4:-4}
dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$video")
n=$(python3 -c "import math;print(max(1,math.ceil($dur/$every)))")
rows=$(python3 -c "import math;print(math.ceil($n/$cols))")
w=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$video")
scale=480; [ "$w" -lt 1500 ] && scale=270
ffmpeg -v error -y -i "$video" -vf "fps=1/$every,scale=$scale:-1,drawtext=text='%{pts\:hms}':x=6:y=6:fontsize=18:fontcolor=yellow:box=1:boxcolor=black@0.6,tile=${cols}x${rows}" -frames:v 1 "$out"
echo "$out ($n frames, ${dur}s)"
