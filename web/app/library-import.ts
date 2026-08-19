export type LibraryRecipe = Record<string, unknown>;
export type LibraryPracticeLog = Record<string, unknown>;

export type StreamedLibrary = {
  schemaVersion: number;
  recipes: LibraryRecipe[];
  practiceLogs: LibraryPracticeLog[];
  assetCount: number;
};

type StreamLibraryOptions = {
  onAsset: (key: string, value: string) => void | Promise<void>;
  onProgress?: (progress: number) => void;
};

const READ_CHUNK_SIZE = 1024 * 1024;

async function* decodeFile(
  file: File,
  onProgress?: (progress: number) => void,
): AsyncGenerator<string> {
  const decoder = new TextDecoder();
  let offset = 0;
  while (offset < file.size) {
    const end = Math.min(offset + READ_CHUNK_SIZE, file.size);
    const bytes = await file.slice(offset, end).arrayBuffer();
    offset = end;
    yield decoder.decode(bytes, { stream: offset < file.size });
    onProgress?.(file.size ? offset / file.size : 1);
  }
  const remainder = decoder.decode();
  if (remainder) yield remainder;
  onProgress?.(1);
}

class JsonStreamReader {
  private readonly chunks: AsyncIterator<string>;
  private buffer = "";
  private offset = 0;
  private done = false;

  constructor(chunks: AsyncIterable<string>) {
    this.chunks = chunks[Symbol.asyncIterator]();
  }

  private async fill(): Promise<boolean> {
    while (this.offset >= this.buffer.length && !this.done) {
      const next = await this.chunks.next();
      this.done = Boolean(next.done);
      this.buffer = next.value ?? "";
      this.offset = 0;
    }
    return this.offset < this.buffer.length;
  }

  async peek(): Promise<string | null> {
    return await this.fill() ? this.buffer[this.offset] : null;
  }

  async take(): Promise<string> {
    if (!await this.fill()) throw new SyntaxError("菜谱包内容不完整");
    return this.buffer[this.offset++];
  }

  async skipWhitespace(): Promise<void> {
    while (true) {
      const char = await this.peek();
      if (char === null || !/\s/.test(char)) return;
      this.offset += 1;
    }
  }

  async expect(expected: string): Promise<void> {
    await this.skipWhitespace();
    const actual = await this.take();
    if (actual !== expected) throw new SyntaxError(`菜谱包格式错误：应为 ${expected}`);
  }

  async readString(): Promise<string> {
    await this.skipWhitespace();
    if (await this.take() !== '"') throw new SyntaxError("菜谱包格式错误：应为字符串");
    const fragments: string[] = [];
    while (true) {
      if (!await this.fill()) throw new SyntaxError("菜谱包内容不完整");
      const quote = this.buffer.indexOf('"', this.offset);
      const slash = this.buffer.indexOf("\\", this.offset);
      const special = quote < 0 ? slash : slash < 0 ? quote : Math.min(quote, slash);
      if (special < 0) {
        fragments.push(this.buffer.slice(this.offset));
        this.offset = this.buffer.length;
        continue;
      }
      fragments.push(this.buffer.slice(this.offset, special));
      this.offset = special + 1;
      if (special === quote) return JSON.parse(`"${fragments.join("")}"`) as string;
      fragments.push("\\", await this.take());
    }
  }

  async readRawValue(): Promise<string> {
    await this.skipWhitespace();
    const first = await this.peek();
    if (first === null) throw new SyntaxError("菜谱包内容不完整");
    if (first === '"') return JSON.stringify(await this.readString());
    if (first !== "{" && first !== "[") {
      let raw = "";
      while (true) {
        const char = await this.peek();
        if (char === null || char === "," || char === "}" || char === "]" || /\s/.test(char)) {
          if (!raw) throw new SyntaxError("菜谱包格式错误：缺少值");
          return raw;
        }
        raw += await this.take();
      }
    }

    let raw = "";
    let depth = 0;
    let inString = false;
    let escaped = false;
    while (true) {
      const char = await this.take();
      raw += char;
      if (inString) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') inString = false;
        continue;
      }
      if (char === '"') inString = true;
      else if (char === "{" || char === "[") depth += 1;
      else if (char === "}" || char === "]") depth -= 1;
      if (depth === 0) return raw;
    }
  }

  async readArray(onValue: (value: unknown) => void | Promise<void>): Promise<void> {
    await this.expect("[");
    await this.skipWhitespace();
    if (await this.peek() === "]") {
      await this.take();
      return;
    }
    while (true) {
      await onValue(JSON.parse(await this.readRawValue()) as unknown);
      await this.skipWhitespace();
      const separator = await this.take();
      if (separator === "]") return;
      if (separator !== ",") throw new SyntaxError("菜谱包数组格式错误");
    }
  }

  async readStringObject(
    onEntry: (key: string, value: string) => void | Promise<void>,
  ): Promise<void> {
    await this.expect("{");
    await this.skipWhitespace();
    if (await this.peek() === "}") {
      await this.take();
      return;
    }
    while (true) {
      const key = await this.readString();
      await this.expect(":");
      const value = await this.readString();
      await onEntry(key, value);
      await this.skipWhitespace();
      const separator = await this.take();
      if (separator === "}") return;
      if (separator !== ",") throw new SyntaxError("菜谱包图片索引格式错误");
    }
  }
}

export async function streamLibraryPackage(
  file: File,
  options: StreamLibraryOptions,
): Promise<StreamedLibrary> {
  const reader = new JsonStreamReader(decodeFile(file, options.onProgress));
  const recipes: LibraryRecipe[] = [];
  const practiceLogs: LibraryPracticeLog[] = [];
  let schemaVersion = 0;
  let assetCount = 0;

  await reader.expect("{");
  await reader.skipWhitespace();
  if (await reader.peek() === "}") throw new Error("菜谱包为空");
  while (true) {
    const key = await reader.readString();
    await reader.expect(":");
    if (key === "schema_version") {
      schemaVersion = Number(JSON.parse(await reader.readRawValue()));
      if (schemaVersion !== 1) throw new Error("不支持的菜谱包版本");
    } else if (key === "recipes") {
      await reader.readArray((value) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) {
          throw new Error("菜谱包中包含无效菜谱");
        }
        recipes.push(value as LibraryRecipe);
      });
    } else if (key === "assets") {
      await reader.readStringObject(async (assetKey, value) => {
        await options.onAsset(assetKey, value);
        assetCount += 1;
      });
    } else if (key === "practice_logs") {
      await reader.readArray((value) => {
        if (value && typeof value === "object" && !Array.isArray(value)) {
          practiceLogs.push(value as LibraryPracticeLog);
        }
      });
    } else {
      await reader.readRawValue();
    }
    await reader.skipWhitespace();
    const separator = await reader.take();
    if (separator === "}") break;
    if (separator !== ",") throw new SyntaxError("菜谱包顶层格式错误");
  }
  if (schemaVersion !== 1 || !recipes.length) throw new Error("菜谱包中没有可导入的菜谱");
  options.onProgress?.(1);
  return { schemaVersion, recipes, practiceLogs, assetCount };
}
