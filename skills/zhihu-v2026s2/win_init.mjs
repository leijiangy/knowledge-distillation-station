// win_init.mjs —— 官方 init_project.mjs 的 Windows 适配版
// 官方脚本依赖 /usr/bin/unzip 与 /bin/bash，Windows 下不存在；
// 本脚本复刻其全部语义：SHA-256 校验、模板复制、官方 skill 安装、config 生成、模板渲染。
// 解压改用 Windows 自带 bsdtar（tar.exe）。
import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, readdir, writeFile, rm, mkdtemp } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';

const skillRoot = 'E:/黑客松/知乎黑客松-知识蒸馏站/skills/zhihu-v2026s2/zhihu-hackathon';
const target = 'E:/黑客松/知乎黑客松-知识蒸馏站/app';
const projectName = '知识蒸馏站';
const appId = '441';
const port = 4173;

function slugify(value) {
  const slug = String(value).normalize('NFKC').toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);
  return slug || 'zhihu-oauth-demo';
}

const projectSlug = slugify(projectName);
const pathId = createHash('sha256').update(target).digest('hex').slice(0, 10);

// 1. 校验随包官方 skill 的 SHA-256（与官方脚本同一期望值）
const bundledZip = path.join(skillRoot, 'assets', 'zhihu-cli-skill.zip');
const sha256 = createHash('sha256').update(await readFile(bundledZip)).digest('hex');
const expected = 'be08e10bbd8f7c554456599e1bdf9e4a4f9216a7624d0b29218e9e4dc1c2f9f3';
if (sha256 !== expected) throw new Error('Bundled official Skill checksum mismatch: ' + sha256);

// 2. 目标目录必须为空
await mkdir(target, { recursive: true });
if ((await readdir(target)).length > 0) throw new Error('Project directory must be empty.');

// 3. 复制 OAuth 版模板
await cp(path.join(skillRoot, 'assets', 'hello-world-oauth'), target, { recursive: true, errorOnExist: true });

// 4. 解压并安装官方 skill 到项目级 .codex/skills/zhihu
const tempDir = await mkdtemp(path.join(skillRoot, 'extract-'));
try {
  await execFile('powershell', [
    '-NoProfile', '-Command',
    `Expand-Archive -LiteralPath '${bundledZip}' -DestinationPath '${tempDir}' -Force`,
  ]);
  const extracted = await readdir(tempDir);
  if (!extracted.includes('zhihu')) throw new Error('解压结果异常: ' + extracted.join(','));
  const skillsDir = path.join(target, '.codex', 'skills');
  await mkdir(skillsDir, { recursive: true });
  await rm(path.join(skillsDir, 'zhihu'), { recursive: true, force: true });
  await cp(path.join(tempDir, 'zhihu'), path.join(skillsDir, 'zhihu'), { recursive: true });
} finally {
  await rm(tempDir, { recursive: true, force: true });
}

// 5. 生成不含密钥的 hackathon.config.json（字段与官方脚本一致）
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

// 6. 渲染模板占位符
for (const rel of ['README.md', 'server.mjs', 'public/index.html']) {
  const p = path.join(target, rel);
  const rendered = (await readFile(p, 'utf8'))
    .replaceAll('__PROJECT_NAME__', projectName)
    .replaceAll('__REDIRECT_URI__', config.oauth.redirectUri || '部署后配置')
    .replaceAll('__PORT__', String(port));
  await writeFile(p, rendered, 'utf8');
}

console.log(JSON.stringify({ ok: true, target, projectSlug, pathId, port, sha256 }, null, 2));
