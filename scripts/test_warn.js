const fs = require('fs');

async function testWarningMsg() {
  const apiKey = process.env.DATA_GO_KR_KEY;
  // Use getWthrWrnMsg
  const url = `http://apis.data.go.kr/1360000/WthrWrnInfoService/getWthrWrnMsg?serviceKey=${encodeURIComponent(apiKey)}&pageNo=1&numOfRows=10&dataType=JSON&stnId=108`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    console.log(JSON.stringify(data.response.body.items.item[0], null, 2));
  } catch(e) {
    console.error(e);
  }
}
testWarningMsg();
