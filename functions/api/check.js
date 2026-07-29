export async function onRequest(context) {
  try {
    // 💡 핵심 해결책: Cloudflare 내부 주소를 참조하면 '과거 스냅샷'에 갇히게 되므로,
    // GitHub Action이 데이터를 밀어넣는 GitHub 원본(Raw) 파일을 직접 확인하여 캐시를 100% 우회합니다.
    const githubRawUrl = `https://raw.githubusercontent.com/jhjhc1483/weather/main/data/weather.json?t=${Date.now()}`;
    
    const response = await fetch(githubRawUrl, {
      headers: {
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        'Pragma': 'no-cache'
      }
    });
    
    if (!response.ok) {
      throw new Error('GitHub 원본 데이터를 불러오지 못했습니다.');
    }
    
    const data = await response.json();
    
    // 프론트엔드가 기다리고 있는 "updated_at" 시간만 추출해서 전달합니다.
    return new Response(JSON.stringify({ updated_at: data.updated_at }), {
      status: 200,
      headers: { 
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store'
      }
    });
  } catch (error) {
    return new Response(JSON.stringify({ error: error.message }), { 
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}
