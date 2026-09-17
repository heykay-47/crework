#!/usr/bin/env bash
set -euo pipefail

output="${1:-fixtures/canonical/feedback-recording.mp4}"
output_dir="$(dirname "$output")"
mkdir -p "$output_dir"

command -v ffmpeg >/dev/null 2>&1 || { printf 'ffmpeg is required\n' >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { printf 'ffprobe is required\n' >&2; exit 1; }
tts_bin="${TTS_BIN:-espeak-ng}"
command -v "$tts_bin" >/dev/null 2>&1 || {
    printf '%s is required; run this script in the project container\n' "$tts_bin" >&2
    exit 1
}

font="${FONT_FILE:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"
test -f "$font" || { printf 'font not found: %s\n' "$font" >&2; exit 1; }

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

make_voice() {
    local name="$1"
    local text="$2"
    "$tts_bin" -v en-us -s 145 -w "$work_dir/$name.wav" "$text"
}

make_text() {
    local name="$1"
    shift
    printf '%s\n' "$*" >"$work_dir/$name.txt"
}

make_clip() {
    local name="$1"
    local duration="$2"
    local color="$3"
    local text_file="$4"
    local voice_file="${5:-}"
    local visual_mode="${6:-normal}"
    local audio_input=()
    if [[ -n "$voice_file" ]]; then
        audio_input=(-i "$work_dir/$voice_file.wav")
    else
        audio_input=(-f lavfi -i "anullsrc=channel_layout=stereo:sample_rate=48000")
    fi

    local visual_filter="[0:v]drawtext=fontfile=$font:textfile=$work_dir/$text_file.txt:fontcolor=white:fontsize=34:line_spacing=18:x=80:y=70[v]"
    if [[ "$visual_mode" == "navbar-overlap" ]]; then
        visual_filter="[0:v]drawtext=fontfile=$font:textfile=$work_dir/$text_file.txt:fontcolor=white:fontsize=34:line_spacing=18:x=80:y=70[base];[base]drawtext=fontfile=$font:text='NORTHSTAR LOGO':fontcolor=white:fontsize=34:x=760:y=210[logo];[logo]drawbox=x=680:y=150:w=450:h=260:color=black@0.90:t=fill[panel];[panel]drawtext=fontfile=$font:text='MENU PANEL':fontcolor=white:fontsize=30:x=710:y=175[v]"
    elif [[ "$visual_mode" == "visual-anomaly" ]]; then
        visual_filter="[0:v]drawtext=fontfile=$font:textfile=$work_dir/$text_file.txt:fontcolor=white:fontsize=34:line_spacing=18:x=80:y=70[base];[base]drawbox=x=480:y=320:w=320:h=100:color=white@0.95:t=fill[button];[button]drawtext=fontfile=$font:text='SUBMIT':fontcolor=black:fontsize=34:x=565:y=350[label];[label]drawbox=x=820:y=390:w=24:h=24:color=yellow@0.95:t=fill:enable='between(t,20,22)'[v]"
    fi

    ffmpeg -hide_banner -loglevel error -y \
        -filter_threads 1 -filter_complex_threads 1 \
        -f lavfi -i "color=c=$color:s=1280x720:r=2" \
        "${audio_input[@]}" \
        -filter_complex "$visual_filter;[1:a]apad[a]" \
        -map '[v]' -map '[a]' -t "$duration" \
        -c:v libx264 -preset medium -crf 30 -pix_fmt yuv420p -threads 1 \
        -c:a aac -b:a 96k -ar 48000 -ac 2 -threads 1 \
        -fflags +bitexact -flags:v +bitexact -flags:a +bitexact -map_metadata -1 \
        -metadata creation_time=1970-01-01T00:00:00Z -max_interleave_delta 0 "$work_dir/$name.mp4"
}

make_text intro $'NORTHSTAR FEEDBACK RECORDING\nCanonical six-case fixture | product review session\nThe client reviews the web experience page by page.'
make_text case_a $'NORTHSTAR / HOME\nHERO CTA\n\n[ Get Started ]\n\nClient request: change the button label to Schedule a Call.'
make_text case_b $'NORTHSTAR / MOBILE NAVIGATION\n\nMENU PANEL\n+------------------+\n| HOME             |\n| PRICING          |\n| CONTACT          |\n+------------------+'
make_text pricing $'NORTHSTAR / PRICING\n\nStarter       Team        Enterprise\n$19           $49         Contact us\n\nClient is looking at this section and reacting to the pricing layout.'
make_text case_d $'NORTHSTAR / DASHBOARD\n\nMENU PANEL             LOGO\n+------------------+    [ N ]\n| OVERVIEW         |\n| REPORTS          |\n| SETTINGS         |\n+------------------+'
make_text analytics $'NORTHSTAR / HOME\n\n[ Schedule a Call ]\n\nClient asks whether analytics already tracks this button.'
make_text visual $'NORTHSTAR / CONTACT\n\nName __________________________\nEmail _________________________'
make_text outro $'END OF CANONICAL FEEDBACK RECORDING\nSix authored cases | frozen fixture v3'

make_voice case_a 'Can we change this button from Get Started to Schedule a Call?'
make_voice case_b 'On mobile, the menu covers the logo. I can see it and the logo disappears when the menu opens.'
make_voice pricing 'I am not really sure about this pricing section. Something just feels off. Maybe we should revisit it.'
make_voice case_d 'On the dashboard page, the mobile menu covers the logo again.'
make_voice analytics 'Do we know whether analytics is already tracking this button?'

make_clip intro 20 '#172033' intro
make_clip case-a 50 '#12304a' case_a case_a
make_clip case-b 50 '#214d43' case_b case_b navbar-overlap
make_clip transition-one 25 '#172033' intro
make_clip pricing 50 '#4b2b45' pricing pricing
make_clip transition-two 15 '#172033' intro
make_clip case-d 40 '#214d43' case_d case_d navbar-overlap
make_clip analytics 40 '#12304a' analytics analytics
make_clip transition-three 10 '#172033' intro
make_clip visual 50 '#4b3a20' visual '' visual-anomaly
make_clip outro 10 '#172033' outro

printf '%s\n' \
    "file '$work_dir/intro.mp4'" \
    "file '$work_dir/case-a.mp4'" \
    "file '$work_dir/case-b.mp4'" \
    "file '$work_dir/transition-one.mp4'" \
    "file '$work_dir/pricing.mp4'" \
    "file '$work_dir/transition-two.mp4'" \
    "file '$work_dir/case-d.mp4'" \
    "file '$work_dir/analytics.mp4'" \
    "file '$work_dir/transition-three.mp4'" \
    "file '$work_dir/visual.mp4'" \
    "file '$work_dir/outro.mp4'" >"$work_dir/concat.txt"

ffmpeg -hide_banner -loglevel error -y \
    -filter_threads 1 -filter_complex_threads 1 \
    -f concat -safe 0 -i "$work_dir/concat.txt" \
    -c:v libx264 -preset medium -crf 30 -pix_fmt yuv420p -threads 1 \
    -c:a aac -b:a 96k -ar 48000 -ac 2 -threads 1 \
    -fflags +bitexact -flags:v +bitexact -flags:a +bitexact -map_metadata -1 \
    -metadata creation_time=1970-01-01T00:00:00Z -max_interleave_delta 0 "$output"

ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$output"
