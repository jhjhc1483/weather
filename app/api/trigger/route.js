import { NextResponse } from 'next/server';

export async function POST(req) {
  try {
    const token = process.env.GIT_TOKEN || process.env.GITHUB_TOKEN;
    if (!token) {
      return NextResponse.json({ error: 'GitHub token not configured' }, { status: 500 });
    }

    const response = await fetch(
      'https://api.github.com/repos/jhjhc1483/weather/actions/workflows/weather-update.yml/dispatches',
      {
        method: 'POST',
        headers: {
          'Authorization': `token ${token}`,
          'Accept': 'application/vnd.github.v3+json',
        },
        body: JSON.stringify({
          ref: 'main'
        })
      }
    );

    if (!response.ok) {
      const errorText = await response.text();
      console.error('GitHub API error:', errorText);
      return NextResponse.json({ error: 'Failed to trigger workflow', details: errorText }, { status: response.status });
    }

    return NextResponse.json({ success: true, message: 'Workflow triggered successfully' });
  } catch (error) {
    console.error('Trigger API error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
