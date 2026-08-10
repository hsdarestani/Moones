from pathlib import Path

path = Path("app/static/pitch.html")
text = path.read_text(encoding="utf-8")

old_en = '<div class="en"><p class="lede reveal d4">Not product-market fit yet. But enough signal to justify a focused traction and monetization phase.</p></div>'
new_en = '<div class="en"><p class="lede reveal d4"><span class="acid">All 410 registrations came from a single ad placement in one ordinary Telegram channel.</span> No multi-channel campaign, growth loop or acquisition optimization yet. This is still not product-market fit — but it is a meaningful signal to justify a focused traction and monetization phase.</p></div>'
old_fa = '<div class="fa"><p class="lede reveal d4">هنوز Product-Market Fit نیست؛ اما برای ورود جدی به فاز ترکشن و درآمدزایی، سیگنال کافی داریم.</p></div>'
new_fa = '<div class="fa"><p class="lede reveal d4"><span class="acid">تمام ۴۱۰ ثبت‌نام فقط از یک تبلیغ در یک کانال تلگرامی معمولی آمده‌اند.</span> هنوز کمپین چندکاناله، Growth Loop یا بهینه‌سازی جدی جذب نداشته‌ایم. این هنوز Product-Market Fit نیست؛ اما برای ورود جدی به فاز ترکشن و درآمدزایی، سیگنال معناداری است.</p></div>'

for old, new in ((old_en, new_en), (old_fa, new_fa)):
    if old not in text:
        raise SystemExit(f"Expected traction copy not found: {old[:80]}")
    text = text.replace(old, new, 1)

old_js = "function step(d){if(locked)return;locked=true;go(index+d);setTimeout(()=>locked=false,720)}addEventListener('wheel',e=>{if(Math.abs(e.deltaY)>18)step(e.deltaY>0?1:-1)},{passive:true});addEventListener('keydown',e=>{if(['ArrowDown','PageDown',' '].includes(e.key)){e.preventDefault();step(1)}if(['ArrowUp','PageUp'].includes(e.key)){e.preventDefault();step(-1)}if(e.key==='Home')go(0);if(e.key==='End')go(slides.length-1)});addEventListener('touchstart',e=>touchY=e.touches[0].clientY,{passive:true});addEventListener('touchend',e=>{const d=touchY-e.changedTouches[0].clientY;if(Math.abs(d)>45)step(d>0?1:-1)},{passive:true});"
new_js = "let touchScrollable=null;function scrollableAncestor(target){let el=target instanceof Element?target:target?.parentElement;while(el&&el!==document.body){const s=getComputedStyle(el),oy=s.overflowY;if((oy==='auto'||oy==='scroll')&&el.scrollHeight>el.clientHeight+2)return el;el=el.parentElement}return null}function step(d){if(locked)return;locked=true;go(index+d);setTimeout(()=>locked=false,720)}addEventListener('wheel',e=>{if(scrollableAncestor(e.target))return;if(Math.abs(e.deltaY)>18)step(e.deltaY>0?1:-1)},{passive:true});addEventListener('keydown',e=>{if(['ArrowDown','PageDown',' '].includes(e.key)){e.preventDefault();step(1)}if(['ArrowUp','PageUp'].includes(e.key)){e.preventDefault();step(-1)}if(e.key==='Home')go(0);if(e.key==='End')go(slides.length-1)});addEventListener('touchstart',e=>{touchY=e.touches[0].clientY;touchScrollable=scrollableAncestor(e.target)},{passive:true});addEventListener('touchend',e=>{const d=touchY-e.changedTouches[0].clientY;if(touchScrollable){touchScrollable=null;return}if(Math.abs(d)>45)step(d>0?1:-1)},{passive:true});addEventListener('touchcancel',()=>touchScrollable=null,{passive:true});"

if old_js not in text:
    raise SystemExit("Expected deck gesture JS not found")
text = text.replace(old_js, new_js, 1)

# Improve native scrolling behavior for the mobile internal panels.
needle = "@media(max-width:600px){"
insert = ".cards,.architecture,.funnel,.layers,.timeline,.matrix,.ask,.sources{overscroll-behavior:contain;-webkit-overflow-scrolling:touch}"
if needle not in text:
    raise SystemExit("Mobile media marker not found")
text = text.replace(needle, insert + needle, 1)

path.write_text(text, encoding="utf-8")
