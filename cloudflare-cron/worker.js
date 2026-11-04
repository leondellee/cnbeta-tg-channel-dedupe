// cnbeta-dedupe 外部定时器（Cloudflare Worker）
//
// 为什么需要它：GitHub Actions 的 schedule 对免费账号节流严重——实测同账号
// 另一个仓库写 */5，真实间隔中位数 4.7 小时。Workers 的 cron 是准的，
// 由它每 5 分钟调一次 workflow_dispatch，Python 逻辑仍然跑在 Actions 上，
// 一行代码都不用改。
//
// 需要的 Secret：GH_TOKEN
//   fine-grained PAT，权限只需对 cnbeta-tg-channel-dedupe 仓库的
//   Actions: Read and write（读运行状态 + 触发）。90 天过期，记得换。
// 可选 Secret：TRIGGER_KEY（浏览器带 ?key= 手动触发一次，便于排查）

const OWNER = 'leondellee';
const REPO = 'cnbeta-tg-channel-dedupe';
const WORKFLOW = 'aggregate.yml';
const REF = 'main';
const API = 'https://api.github.com';

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(tick(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (env.TRIGGER_KEY && url.searchParams.get('key') === env.TRIGGER_KEY) {
      const result = await tick(env, true);
      return json(result, result.ok ? 200 : 500);
    }
    return new Response('cnbeta-dedupe cron worker is running\n', { status: 200 });
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

function gh(env, path, init = {}) {
  return fetch(API + path, {
    ...init,
    headers: {
      Authorization: 'Bearer ' + env.GH_TOKEN,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'cnbeta-dedupe-cron-worker',
      ...(init.headers || {}),
    },
  });
}

async function tick(env, force = false) {
  const at = new Date().toISOString();
  try {
    if (!env.GH_TOKEN) return { ok: false, error: 'GH_TOKEN 未配置', at };

    // 上一轮还没跑完就别叠，避免并发抢同一份 state
    if (!force) {
      const runsRes = await gh(
        env,
        `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=3`,
      );
      if (runsRes.ok) {
        const list = (await runsRes.json()).workflow_runs || [];
        const busy = list.find((r) => r.status === 'queued' || r.status === 'in_progress');
        if (busy) return { ok: true, action: 'skip-busy', busy_run: busy.id, at };
      }
    }

    const res = await gh(env, `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ref: REF }),
    });

    return {
      ok: res.status === 204,
      action: res.status === 204 ? 'dispatched' : 'dispatch-failed',
      status: res.status,
      at,
    };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e), at };
  }
}
