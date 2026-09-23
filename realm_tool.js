// Доступ к базе osu!lazer (client.realm): коллекции и список карт.
// Пишет только если формат файла совпадает с поддерживаемым - чтобы никогда не "апгрейдить" чужую базу.
const Realm = require('realm');
const fs = require('fs');

const SUPPORTED_FORMAT = 24;
const [cmd, realmPath, arg] = process.argv.slice(2);

function fileFormat(path) {
  const fd = fs.openSync(path, 'r');
  const h = Buffer.alloc(24);
  fs.readSync(fd, h, 0, 24, 0);
  fs.closeSync(fd);
  if (h.toString('latin1', 16, 20) !== 'T-DB') return null;
  return h[20 + (h[23] & 1)];
}

(async () => {
  const fmt = fileFormat(realmPath);
  if (fmt !== SUPPORTED_FORMAT) {
    console.error(`UNSUPPORTED_FORMAT ${fmt}: база osu! в формате ${fmt}, инструмент поддерживает ${SUPPORTED_FORMAT}. ` +
      'Обнови osu!trainer (или osu!lazer) - запись отменена, база не тронута.');
    process.exit(3);
  }
  const readOnly = cmd === 'list' || cmd === 'local';
  const realm = await Realm.open({ path: realmPath, readOnly });
  if (cmd === 'list') {
    const out = realm.objects('BeatmapCollection').map(c => ({
      name: c.Name, count: c.BeatmapMD5Hashes.length,
    }));
    console.log(JSON.stringify(out));
  } else if (cmd === 'local') {
    const out = [];
    for (const b of realm.objects('Beatmap')) {
      if (!b.MD5Hash || (b.BeatmapSet && b.BeatmapSet.DeletePending)) continue;
      out.push({
        md5: b.MD5Hash, fileHash: b.Hash, onlineId: b.OnlineID,
        setId: b.BeatmapSet ? b.BeatmapSet.OnlineID : 0,
        sr: +b.StarRating.toFixed(2), bpm: +b.BPM.toFixed(0), len: +(b.Length / 1000).toFixed(0),
        diff: b.DifficultyName, ruleset: b.Ruleset ? b.Ruleset.ShortName : '?',
        title: b.Metadata ? b.Metadata.Title : '', artist: b.Metadata ? b.Metadata.Artist : '',
        tags: b.Metadata ? b.Metadata.Tags : '',
      });
    }
    fs.writeFileSync(arg, JSON.stringify(out));
    console.log(out.length);
  } else if (cmd === 'add') {
    const payload = JSON.parse(fs.readFileSync(arg, 'utf8'));   // [{name, hashes:[]}]
    const report = [];
    realm.write(() => {
      for (const { name, hashes } of payload) {
        const existing = realm.objects('BeatmapCollection').filtered('Name == $0', name)[0];
        if (existing) {
          let added = 0;
          for (const h of hashes)
            if (!existing.BeatmapMD5Hashes.includes(h)) { existing.BeatmapMD5Hashes.push(h); added++; }
          existing.LastModified = new Date();
          report.push({ name, added, total: existing.BeatmapMD5Hashes.length, created: false });
        } else {
          realm.create('BeatmapCollection', {
            ID: new Realm.BSON.UUID(), Name: name, BeatmapMD5Hashes: hashes, LastModified: new Date(),
          });
          report.push({ name, added: hashes.length, total: hashes.length, created: true });
        }
      }
    });
    console.log(JSON.stringify(report));
  } else if (cmd === 'remove') {
    let n = 0;
    realm.write(() => {
      const c = realm.objects('BeatmapCollection').filtered('Name == $0', arg);
      n = c.length;
      realm.delete(c);
    });
    console.log(JSON.stringify({ removed: n }));
  } else {
    console.error('usage: list | local <out.json> | add <payload.json> | remove <name>');
    process.exit(2);
  }
  realm.close();
  process.exit(0);
})().catch(e => { console.error('ERR ' + e.message); process.exit(1); });
