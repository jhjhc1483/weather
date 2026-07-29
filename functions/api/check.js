export async function onRequest(context) {
  const { env } = context;
  const githubRepo = 'jhjhc1483/weather';

  try {
    const response = await fetch(`https://api.github.com/repos/${githubRepo}/actions/runs?per_page=1`, {
      headers: {
        'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Cloudflare-Pages'
      }
    });
    
    const data = await response.json();
    const latestRun = data.workflow_runs?.[0];
    
    return new Response(JSON.stringify({ 
      status: latestRun ? latestRun.status : 'unknown',
      conclusion: latestRun ? latestRun.conclusion : null
    }), { 
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  } catch (error) {
    return new Response(JSON.stringify({ error: error.message }), { 
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}
