import { afterEach, describe, expect, it, vi } from 'vitest';
vi.mock('$lib/constants',()=>({WEBUI_API_BASE_URL:'https://oi.invalid/api/v1'}));
vi.mock('$lib/apis/customization',()=>({oiApi:vi.fn()}));
import { runBrowserCompletion,cancelBrowserCompletion } from './browserConnections';

const data={channel:'owner:session:call',model:{id:'oi:binding',connection:{id:'browser-a',transport:'browser',revision:3}},path:'/chat/completions',form_data:{model:'same-model',stream:true,messages:[{role:'user',content:'synthetic'}]}};
const config=()=>Response.json({id:'browser-a',base_url:'https://provider.invalid/v1',headers:{Authorization:'Bearer synthetic-provider'}});
afterEach(()=>{cancelBrowserCompletion();vi.unstubAllGlobals();});

describe('browser execution boundary',()=>{
    it('preserves arbitrary UTF8/SSE chunk boundaries and stable transport routing',async()=>{
        vi.stubGlobal('localStorage',{token:'synthetic-oi'});
        const source='data: {"choices":[{"delta":{"content":"汉字"}}]}\n\ndata: {"usage":{"prompt_tokens":123}}\n\n';
        const bytes=new TextEncoder().encode(source);
        const offset=bytes.indexOf(0xe6)+1;
        const fetch=vi.fn().mockResolvedValueOnce(config()).mockResolvedValueOnce(new Response(new ReadableStream({start(controller){controller.enqueue(bytes.slice(0,offset));controller.enqueue(bytes.slice(offset,offset+2));controller.enqueue(bytes.slice(offset+2));controller.close();}}),{headers:{'content-type':'text/event-stream'}}));
        vi.stubGlobal('fetch',fetch);
        const socket={emit:vi.fn()},ack=vi.fn();
        await runBrowserCompletion(data,socket,ack);
        expect(fetch.mock.calls[0][0]).toBe('https://oi.invalid/api/v1/connections/browser-a/browser-config?revision=3');
        expect(fetch.mock.calls[0][1].cache).toBe('no-store');
        expect(fetch.mock.calls[1][0]).toBe('https://provider.invalid/v1/chat/completions');
        expect(JSON.parse(fetch.mock.calls[1][1].body).model).toBe('same-model');
        expect(fetch.mock.calls[1][1].credentials).toBe('omit');
        expect(socket.emit.mock.calls.filter(([,part])=>typeof part==='string').map(([,part])=>part).join('')).toBe(source);
        expect(socket.emit).toHaveBeenLastCalledWith(data.channel,{done:true});
        expect(ack).toHaveBeenCalledOnce();
    });
    it('passes through429 and Retry-After without retrying',async()=>{
        vi.stubGlobal('localStorage',{token:'synthetic-oi'});
        const fetch=vi.fn().mockResolvedValueOnce(config()).mockResolvedValueOnce(Response.json({error:'limited'},{status:429,headers:{'Retry-After':'9'}}));
        vi.stubGlobal('fetch',fetch);const ack=vi.fn();
        await runBrowserCompletion({...data,path:'/responses'}, {emit:vi.fn()},ack);
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(ack.mock.calls[0][0]).toMatchObject({status_code:429,headers:{'retry-after':'9'}});
    });
    it('aborts pending requests when the owner browser disconnects',async()=>{
        vi.stubGlobal('localStorage',{token:'synthetic-oi'});
        let reached!:()=>void;const ready=new Promise<void>(resolve=>reached=resolve);
        const fetch=vi.fn().mockResolvedValueOnce(config()).mockImplementationOnce((_url,{signal})=>new Promise((_resolve,reject)=>{
            signal.addEventListener('abort',()=>reject(new Error('synthetic abort')));reached();
        }));
        vi.stubGlobal('fetch',fetch);const socket={emit:vi.fn()},ack=vi.fn();
        const request=runBrowserCompletion(data,socket,ack);await ready;cancelBrowserCompletion();await request;
        expect(ack.mock.calls[0][0].status_code).toBe(502);
        expect(socket.emit).toHaveBeenLastCalledWith(data.channel,{done:true});
    });
});
