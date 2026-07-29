export async function onRequest(context) {
  const { env } = context;
  
  try {
    // 💡 깃허브 API를 이용해 비공개 저장소의 파일에 안전하게 접근합니다.
    const apiUrl = 'https://api.github.com/repos/jhjhc1483/weather/contents/data/weather.json';
    
    const response = await fetch(apiUrl, {
      headers: {
        'Authorization': `Bearer ${env.GITHUB_TOKEN}`, // 환경 변수에서 토큰을 가져와 인증
        'Accept': 'application/vnd.github.v3.raw',   // 인코딩된 데이터 대신 JSON 원본을 바로 받도록 요청
        'User-Agent': 'Cloudflare-Pages',
        'Cache-Control': 'no-store, no-cache, must-revalidate', // 캐시 절대 금지
        'Pragma': 'no-cache'
      }
    });
    
    if (!response.ok) {
      throw new Error('GitHub API 통신 오류: ' + response.status);
    }
    
    // 가져온 JSON 파일에서 데이터를 추출합니다.
    const data = await response.json();
    
    // 프론트엔드가 기다리고 있는 "updated_at" 시간만 전달합니다.
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
