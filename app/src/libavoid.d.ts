declare module "libavoid-js" {
  export class AvoidLib {
    static load(url: string): Promise<void>;
    static getInstance(): any;
  }
}
