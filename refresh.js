// 사이트의 [지금 갱신] 버튼 → GitHub Actions 워크플로 수동 실행(workflow_dispatch).
//
// Vercel 프로젝트 환경변수:
//   GH_TOKEN        저장소의 Actions 읽기/쓰기 권한이 있는 fine-grained PAT   (필수)
//   GH_REPO         "소유자/저장소"  예: chae/weather                          (필수)
//   GH_WORKFLOW     워크플로 파일명. 기본 update.yml                          (선택)
//   GH_REF          브랜치. 기본 main                                          (선택)
//   REFRESH_SECRET  설정하면 이 값을 x-refresh-key 헤더로 보내야 실행됨        (권장)
//
// 토큰은 서버 환경변수로만 존재하고 브라우저로 내려가지 않습니다.

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "POST 로 호출하세요" });
  }

  const secret = process.env.REFRESH_SECRET;
  if (secret && req.headers["x-refresh-key"] !== secret) {
    return res.status(401).json({ error: "실행 암호가 필요합니다" });
  }

  const token = process.env.GH_TOKEN;
  const repo = process.env.GH_REPO;
  const workflow = process.env.GH_WORKFLOW || "update.yml";
  const ref = process.env.GH_REF || "main";

  if (!token || !repo) {
    return res.status(500).json({
      error: "GH_TOKEN / GH_REPO 환경변수가 설정되지 않았습니다",
    });
  }

  try {
    const r = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
          "User-Agent": "weather-board",
        },
        body: JSON.stringify({ ref }),
      }
    );

    if (r.status === 204) {
      return res.status(202).json({
        ok: true,
        message: "수집을 시작했습니다. 반영까지 1~3분 걸립니다.",
        runsUrl: `https://github.com/${repo}/actions/workflows/${workflow}`,
      });
    }

    const detail = await r.text();
    return res.status(r.status).json({ ok: false, error: detail.slice(0, 300) });
  } catch (e) {
    return res.status(502).json({ ok: false, error: String(e).slice(0, 300) });
  }
};
