# Extract the serialized FileDescriptorProto embedded in each generated _pb.rb.
# Only accepts a plain double-quoted literal with no interpolation before eval.
out_dir = ARGV[1]
Dir[File.join(ARGV[0], "*_pb.rb")].sort.each do |path|
  line = File.readlines(path, chomp: true).find { |l| l.start_with?('descriptor_data = "') }
  next unless line  # DSL-style stubs (Keynote, Numbers, charts) are not needed for Pages
  literal = line.sub(/\Adescriptor_data = /, "")
  abort("unexpected literal in #{path}") unless literal =~ /\A"(?:[^"\\]|\\.)*"\z/ && !literal.include?('#{')
  bytes = eval(literal).b
  File.binwrite(File.join(out_dir, File.basename(path, "_pb.rb") + ".fdp"), bytes)
end
puts Dir[File.join(out_dir, "*.fdp")].size
