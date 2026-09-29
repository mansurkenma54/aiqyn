#!/usr/bin/env bash
# AIQYN промо видеосының AI кадрлары (Higgsfield / Google Veo 3.1 Lite).
#
# Әр кадр 6 секунд, 16:9. Дыбыс генерацияланбайды — диктор мен эффектілер
# бізде бөлек дайын (scripts/make_narration.py).
#
#   bash scripts/make_promo_clips.sh
#
# Нәтиже: reports/promo/clip*.mp4

set -u
OUT="$(cd "$(dirname "$0")/.." && pwd)/reports/promo"
mkdir -p "$OUT"
MODEL=veo3_1_lite

gen () {
  local name="$1"; shift
  local prompt="$1"; shift
  echo "=== $name ==="
  higgsfield generate create "$MODEL" \
    --prompt "$prompt" \
    --duration 6 \
    --aspect_ratio 16:9 \
    --wait --wait-timeout 15m --wait-interval 10s \
    2>&1 | tee "$OUT/$name.log"
}

# 1. ПРОБЛЕМА — бұзылған жол
gen clip1_problem \
"Cinematic dashcam point of view driving slowly along a narrow Central Asian \
city street at golden hour. Low warm sun, single-storey shops with colourful \
signs on both sides, a few pedestrians. The car approaches and rolls through a \
large broken asphalt pothole with crumbling edges and exposed grey gravel; the \
camera jolts hard on impact. Documentary realism, natural colours, 24mm wide \
lens, subtle handheld shake. No text, no captions, no logos."

# 2. ҚОСЫМША ЖАБДЫҚ КЕРЕК ЕМЕС — тек телефон
gen clip2_phone \
"Close-up interior car shot. A hand clips an ordinary smartphone into a simple \
suction windshield mount and taps the screen once. Warm morning light through \
the windscreen, shallow depth of field, an empty city street visible ahead \
through the glass. Clean modern documentary style, photorealistic, calm and \
purposeful. No text, no captions, no user interface visible."

# 3. ИНТЕРНЕТСІЗ ЖҰМЫС — карта, оффлайн
gen clip3_offline \
"Interior of a parked car at dusk. A laptop rests on the passenger seat showing \
a dark map interface with a chain of small glowing red markers along one street. \
The screen glow softly lights the cabin and the driver's hands. Outside the side \
window, a quiet Central Asian city street with warm street lamps. Cinematic, \
shallow depth of field, calm technical mood. No readable text."

# 4. СМАРТ СИТИ НӘТИЖЕ — жөнделген жол
gen clip4_smartcity \
"Aerial drone shot slowly rising over a Central Asian city at sunrise. Freshly \
repaved smooth dark asphalt streets below, crisp white road markings, a few cars \
moving calmly. Warm golden light, long soft shadows, distant mountains on the \
horizon. Subtle clean cyan data points glow briefly along the road network then \
fade out. Optimistic, cinematic, high detail. No text, no captions."

echo
echo "Дайын. Файлдар: $OUT"
ls -la "$OUT"
