const switcher = document.getElementById('language');
let language = 'en';
function setLanguage(next) {
 language = next;
 document.documentElement.lang = next === 'zh' ? 'zh-Hant' : 'en';
 document.querySelectorAll('[data-language]').forEach(el => { el.hidden = el.dataset.language !== next; });
 switcher.textContent = next === 'en' ? '繁體中文' : 'English';
 switcher.setAttribute('aria-label', next === 'en' ? 'Switch to Traditional Chinese' : '切換至英文');
 try { localStorage.setItem('detour-language',next); } catch {}
}
switcher.addEventListener('click', () => setLanguage(language === 'en' ? 'zh' : 'en'));
let preferred; try { preferred = localStorage.getItem('detour-language'); } catch {}
if(preferred === 'zh') setLanguage('zh');

const clips = [...document.querySelectorAll('video')];
const visibleClips = new Set();
clips.forEach(video => { video.muted = true; video.defaultMuted = true; });
function syncClips() {
 clips.forEach(video => {
  if (visibleClips.has(video) && !document.hidden) video.play().catch(() => {});
  else video.pause();
 });
}
if ('IntersectionObserver' in window) {
 const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => { if(entry.isIntersecting) visibleClips.add(entry.target); else visibleClips.delete(entry.target); });
  syncClips();
 }, {threshold:0.2});
 clips.forEach(video => observer.observe(video));
} else {
 clips.forEach(video => visibleClips.add(video)); syncClips();
}
let filmPlayer, filmReady = false, filmVisible = false;
function syncFilm() {
 if(!filmReady) return;
 if(filmVisible && !document.hidden) filmPlayer.playVideo(); else filmPlayer.pauseVideo();
}
const frame = document.getElementById('project-film');
if ('IntersectionObserver' in window) {
 const observer = new IntersectionObserver(entries => {
  filmVisible = entries[0].isIntersecting;
  syncFilm();
 },{threshold:0.3});
 observer.observe(frame);
} else filmVisible = true;
window.onYouTubeIframeAPIReady = () => {
 filmPlayer = new YT.Player('project-film', {events:{
  onReady: event => {filmReady = true; event.target.mute(); syncFilm();},
  onError: () => { frame.dataset.playback = 'unavailable'; }
 }});
};
const api = document.createElement('script');
api.src = 'https://www.youtube.com/iframe_api'; api.async = true;
document.head.appendChild(api);
document.addEventListener('visibilitychange', () => { syncClips(); syncFilm(); });
