export async function onRequest(context) {
  try {
    // CDN 캐시를 완벽히 우회하기 위해 타임스탬프와 강력한 헤더를 추가하여 현재 도메인의 데이터를 가져옵니다.
    const url = new URL(context.request.url);
    const dataUrl = `${url.origin}/data/weather.json?t=${Date.now()}`;
    
    const response = await fetch(dataUrl, {
      headers: {
        'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
      }
    });
    
    if (!response.ok) {
      throw new Error('데이터를 불러오지 못했습니다.');
    }
    
    const data = await response.json();
    
    // 프론트엔드(script.js)가 애타게 기다리고 있는 { "updated_at": "..." } 형식으로 반환합니다.
    return new Response(JSON.stringify({ updated_at: data.updated_at }), {
      status: 200,
      headers: { 
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store' // 이 API 응답 자체도 브라우저에 캐시되지 않도록 설정
      }
    });
  } catch (error) {
    return new Response(JSON.stringify({ error: error.message }), { 
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}
