// Run me from the Shell tab:  node scripts/vault-stats.js
const fs = require('fs'), path = require('path');
const walk = d => fs.readdirSync(d).flatMap(n => { const p = path.join(d, n); try { return walk(p); } catch { return [p]; } });
const notes = walk('/').filter(p => p.endsWith('.md'));
let words = 0, links = 0;
for (const p of notes) { const t = fs.readFileSync(p, 'utf8'); words += (t.match(/\S+/g) || []).length; links += (t.match(/\[\[[^\]]+\]\]/g) || []).length; }
console.log(`${notes.length} notes, ${words} words, ${links} wiki links`);
