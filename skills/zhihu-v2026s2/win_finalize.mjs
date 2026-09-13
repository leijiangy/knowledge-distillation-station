// win_finalize.mjs —— 初始化第二阶段：生成 hackathon.config.json + 渲染模板占位符
// （解压/复制已由 CMD 完成，node 子进程解压在沙箱下不可见，故拆两步）
import { createHash } from 'node:crypto';
import { readFile, writeFile, access } from 'node:fs/promises';
import path from 'node:path';

const target = 'E:/黑客松/知乎黑客松-知识蒸馏站/app';
const projectName = '知识蒸馏站';
const appId = '441';
const port = 4173;

function slugify(value) {
  const slug = String(value).normalize('NFKC').toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);
  return slug || 'zhihu-oauth-demo';
}

// 官方 skill 必须已解压到位
await access(path.join(target, '.codex', 'skills', 'zhihu', 'SKILL.md'));

const projectSlug = slugify(projectName);
const pathId = createHash('sha256').update(target).digest('hex').slice(0, 10);
const config = {
  schemaVersion: 1,
  projectName,
  projectSlug,
  oauth: {
    enabled: true,
    appId,
    redirectUri: null,
    credentialService: `zhihu-hackathon:${projectSlug}:${pathId}`,
    credentialAccount: 'oauth-app-key',
  },
  host: '127.0.0.1',
  port,
};
await writeFile(path.join(target, 'hackathon.config.json'), JSON.stringify(config, null, 2) + '\n', 'utf8');

for (const rel of ['README.md', 'server.mjs', 'public/index.html']) {
  const p = path.join(target, rel);
  const rendered = (await readFile(p, 'utf8'))
    .replaceAll('__PROJECT_NAME__', projectName)
    .replaceAll('__REDIRECT_URI__', '部署后配置')
    .replaceAll('__PORT__', String(port));
  await writeFile(p, rendered, 'utf8');
}

console.log(JSON.stringify({ ok: true, projectSlug, pathId, port }, null, 2));
